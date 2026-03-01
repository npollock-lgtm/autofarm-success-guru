"""
LLM Router for AutoFarm Zero — Success Guru Network v6.0.

Routes LLM requests to the best available provider with graceful degradation.
Priority: Ollama (local, free, unlimited) -> Groq (fast, rate-limited) -> Cached (emergency)

RULE: All LLM calls MUST go through LLMRouter.generate(). Direct Ollama/Groq
calls are forbidden outside this router.

Task routing:
- Script generation: Ollama (primary) — bulk work, no rate limit
- Brand safety scoring: Ollama — needs nuance, acceptable at 8B
- Caption variation: Ollama — simple rewording task
- Hashtag generation: Ollama — pattern matching, 8B handles fine
- Brand config generation: Groq 70B — complex, rare (1/month max)
- Hook optimisation: Groq 70B — needs sophisticated analysis, rare
- Emergency fallback: Cached templates + simple variations
"""

import os
import time
import json
import logging
import random
from datetime import datetime
from enum import Enum
from pathlib import Path

from database.db import Database

logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    """Available LLM providers in order of preference."""
    OLLAMA = "ollama"
    GROQ = "groq"
    CACHED = "cached"


class LLMRouter:
    """
    Routes LLM requests to the best available provider.

    Manages provider health, Groq rate limit tracking, and automatic
    failover between providers. Logs all requests for monitoring.

    Provider strategy:
    - Ollama: Primary for all routine tasks. Local, free, unlimited.
    - Groq: Fallback for complex tasks or when Ollama is down.
    - Cached: Emergency fallback when both are unavailable.
    """

    # Groq free tier limits (verified current)
    GROQ_DAILY_LIMITS = {
        'llama-3.3-70b-versatile': {
            'rpd': 1000,
            'tpd': 100000,
            'tpm': 12000,
            'rpm': 30,
        },
        'llama-3.1-8b-instant': {
            'rpd': 14400,
            'tpd': 500000,
            'tpm': 6000,
            'rpm': 30,
        },
    }

    # Tasks that benefit from the 70B model
    COMPLEX_TASKS = {
        'brand_config_generation',
        'hook_optimisation',
        'weekly_analysis',
    }

    # Safety margin — never exceed 80% of limits
    SAFETY_MARGIN = 0.8

    def __init__(self):
        """
        Initializes the LLM Router with provider health and usage tracking.

        Side effects:
            Loads API keys from environment.
            Initializes usage counters.
        """
        self.db = Database()
        self.groq_api_key = os.getenv('GROQ_API_KEY', '')
        self.ollama_host = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
        self.ollama_model = os.getenv('OLLAMA_MODEL', 'llama3.1:8b')

        # Provider health tracking
        self.ollama_healthy = True
        self.groq_healthy = bool(self.groq_api_key)

        # Groq daily usage tracking
        self.groq_usage_today = {'requests': 0, 'tokens': 0}
        self.groq_last_reset = datetime.utcnow().date()

        # Cached responses directory
        self.cached_responses_dir = Path(
            os.getenv('APP_DIR', '/app')
        ) / 'config' / 'cached_responses'

    def generate(self, prompt: str, task_type: str,
                 max_tokens: int = 1000,
                 temperature: float = 0.7,
                 brand_id: str = None) -> dict:
        """
        Routes to best provider for this task and generates a response.

        Parameters:
            prompt: The prompt text to send to the LLM.
            task_type: Category of task for routing decisions.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature (0.0-1.0).
            brand_id: Optional brand context for logging.

        Returns:
            Dictionary with keys: text, provider, tokens_used, latency_ms.

        Raises:
            RuntimeError: If all providers fail and no cached response available.
        """
        provider = self._select_provider(task_type, max_tokens)
        start_time = time.time()
        result = None
        error_msg = None

        try:
            if provider == LLMProvider.OLLAMA:
                result = self._call_ollama(prompt, max_tokens, temperature)
            elif provider == LLMProvider.GROQ:
                result = self._call_groq(prompt, max_tokens, temperature)
            else:
                result = self._get_cached_response(task_type, brand_id)
        except Exception as e:
            error_msg = str(e)
            logger.warning(
                f"{provider.value} failed: {e}. Attempting fallback.",
                extra={'task_type': task_type, 'brand_id': brand_id}
            )

            # Attempt fallback chain
            if provider == LLMProvider.OLLAMA:
                try:
                    if self._groq_within_limits(max_tokens):
                        result = self._call_groq(prompt, max_tokens, temperature)
                    else:
                        result = self._get_cached_response(task_type, brand_id)
                except Exception as e2:
                    logger.warning(f"Groq fallback also failed: {e2}")
                    result = self._get_cached_response(task_type, brand_id)
            elif provider == LLMProvider.GROQ:
                result = self._get_cached_response(task_type, brand_id)

        latency_ms = int((time.time() - start_time) * 1000)

        if result is None:
            raise RuntimeError(
                f"All LLM providers failed for task_type={task_type}. "
                f"Last error: {error_msg}"
            )

        result['latency_ms'] = latency_ms

        # Log the request
        self._log_request(
            provider=result.get('provider', 'unknown'),
            task_type=task_type,
            brand_id=brand_id,
            tokens_used=result.get('tokens_used', 0),
            latency_ms=latency_ms,
            success=True,
        )

        return result

    def _select_provider(self, task_type: str, max_tokens: int) -> LLMProvider:
        """
        Selects the best provider for the given task.

        Decision logic:
        1. Complex/rare tasks -> Groq (if available and within limits)
        2. Everything else -> Ollama (if healthy)
        3. Both down -> Cached

        Parameters:
            task_type: Category of task.
            max_tokens: Estimated max tokens for the request.

        Returns:
            LLMProvider enum value.
        """
        # Complex tasks prefer Groq 70B
        if task_type in self.COMPLEX_TASKS and self._groq_within_limits(max_tokens):
            return LLMProvider.GROQ

        # Routine tasks use Ollama
        if self.ollama_healthy:
            return LLMProvider.OLLAMA

        # Ollama down, try Groq
        if self._groq_within_limits(max_tokens):
            return LLMProvider.GROQ

        # Everything down, use cached
        return LLMProvider.CACHED

    def _groq_within_limits(self, estimated_tokens: int) -> bool:
        """
        Checks if a Groq call would stay within free tier limits.

        Parameters:
            estimated_tokens: Estimated tokens for the request.

        Returns:
            True if the call is safe to make.
        """
        if not self.groq_healthy or not self.groq_api_key:
            return False

        self._maybe_reset_daily_counters()
        limits = self.GROQ_DAILY_LIMITS['llama-3.3-70b-versatile']

        return (
            self.groq_usage_today['requests'] < limits['rpd'] * self.SAFETY_MARGIN
            and self.groq_usage_today['tokens'] + estimated_tokens < limits['tpd'] * self.SAFETY_MARGIN
        )

    def _call_ollama(self, prompt: str, max_tokens: int,
                     temperature: float) -> dict:
        """
        Calls the local Ollama instance.

        Parameters:
            prompt: Prompt text.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.

        Returns:
            Dict with text, provider, tokens_used.

        Raises:
            Exception: On timeout or connection error. Marks Ollama unhealthy.
        """
        import requests

        try:
            start = time.time()
            response = requests.post(
                f'{self.ollama_host}/api/generate',
                json={
                    'model': self.ollama_model,
                    'prompt': prompt,
                    'stream': False,
                    'options': {
                        'num_predict': max_tokens,
                        'temperature': temperature,
                    }
                },
                timeout=120  # 2 min timeout for 8B on ARM
            )
            response.raise_for_status()
            data = response.json()
            self.ollama_healthy = True
            return {
                'text': data['response'],
                'provider': 'ollama',
                'tokens_used': data.get('eval_count', 0),
                'latency_ms': int((time.time() - start) * 1000),
            }
        except Exception as e:
            self.ollama_healthy = False
            raise

    def _call_groq(self, prompt: str, max_tokens: int,
                   temperature: float) -> dict:
        """
        Calls the Groq API with rate tracking.

        Parameters:
            prompt: Prompt text.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.

        Returns:
            Dict with text, provider, tokens_used.

        Raises:
            Exception: On API error. Marks Groq unhealthy on 429.

        Side effects:
            Increments daily usage counters.
        """
        import requests

        try:
            start = time.time()
            response = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {self.groq_api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': 'llama-3.3-70b-versatile',
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': max_tokens,
                    'temperature': temperature,
                },
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            tokens_used = data['usage']['total_tokens']

            # Track usage
            self.groq_usage_today['requests'] += 1
            self.groq_usage_today['tokens'] += tokens_used
            self.groq_healthy = True

            return {
                'text': data['choices'][0]['message']['content'],
                'provider': 'groq',
                'tokens_used': tokens_used,
                'latency_ms': int((time.time() - start) * 1000),
            }
        except Exception as e:
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 429:
                self.groq_healthy = False
            raise

    def _get_cached_response(self, task_type: str,
                             brand_id: str = None) -> dict:
        """
        Returns a template response from cached files.

        Emergency fallback when both Ollama and Groq are unavailable.
        Templates stored in config/cached_responses/{task_type}.json.

        Parameters:
            task_type: Category of task to find templates for.
            brand_id: Optional brand for brand-specific templates.

        Returns:
            Dict with text, provider='cached', tokens_used=0.
        """
        # Map task types to cached response files
        file_map = {
            'script_generation': 'script_generation.json',
            'caption_variation': 'caption_variation.json',
            'hashtag_generation': 'hashtag_generation.json',
        }

        filename = file_map.get(task_type)
        if filename:
            cache_path = self.cached_responses_dir / filename
            if cache_path.exists():
                try:
                    with open(cache_path, 'r') as f:
                        templates = json.load(f)

                    text = self._select_cached_template(templates, task_type, brand_id)
                    return {
                        'text': text,
                        'provider': 'cached',
                        'tokens_used': 0,
                        'latency_ms': 0,
                    }
                except Exception as e:
                    logger.error(f"Failed to load cached response: {e}")

        # Absolute fallback — return a generic placeholder
        return {
            'text': self._generate_minimal_fallback(task_type),
            'provider': 'cached',
            'tokens_used': 0,
            'latency_ms': 0,
        }

    def _select_cached_template(self, templates: dict, task_type: str,
                                brand_id: str = None) -> str:
        """
        Selects an appropriate template from the cached responses.

        Parameters:
            templates: Loaded JSON template data.
            task_type: Task type for template selection.
            brand_id: Optional brand for brand-specific selection.

        Returns:
            Template text string.
        """
        if task_type == 'script_generation' and brand_id:
            brand_scripts = templates.get(brand_id, [])
            if brand_scripts:
                script = random.choice(brand_scripts)
                return f"{script['hook']}\n\n{script['body']}\n\n{script['cta']}"

        elif task_type == 'caption_variation' and brand_id:
            cap_templates = templates.get('caption_templates', {})
            brand_caps = cap_templates.get(brand_id, {})
            openings = brand_caps.get('opening_variants', ['Content about this topic.'])
            ctas = brand_caps.get('cta_variants', ['Follow for more.'])
            return f"{random.choice(openings)}\n\n{random.choice(ctas)}"

        elif task_type == 'hashtag_generation' and brand_id:
            pools = templates.get('hashtag_pools', {})
            brand_pool = pools.get(brand_id, {})
            core = brand_pool.get('core', ['#motivation', '#success'])
            rotating = brand_pool.get('rotating', [])
            tags = random.sample(core, min(3, len(core)))
            if rotating:
                tags += random.sample(rotating, min(2, len(rotating)))
            return ' '.join(tags)

        # Fallback for unknown task/brand combinations
        return "Generated content placeholder."

    def _generate_minimal_fallback(self, task_type: str) -> str:
        """
        Generates a minimal fallback response when no templates available.

        Parameters:
            task_type: The task type that needs a response.

        Returns:
            Minimal fallback text.
        """
        fallbacks = {
            'script_generation': (
                "Success requires discipline.\n\n"
                "Every day presents a choice. You can follow the path of least "
                "resistance, or you can choose growth. The difference between "
                "those who achieve their goals and those who do not is not talent "
                "or luck. It is the daily commitment to showing up.\n\n"
                "Follow for more insights."
            ),
            'caption_variation': "Discover the truth about success. Follow for more.",
            'hashtag_generation': "#success #motivation #mindset #growth #psychology",
        }
        return fallbacks.get(task_type, "Content generated by AutoFarm Zero.")

    def _maybe_reset_daily_counters(self) -> None:
        """
        Resets Groq daily usage counters at midnight UTC.

        Side effects:
            Resets counters if the current date is past the last reset date.
        """
        today = datetime.utcnow().date()
        if today > self.groq_last_reset:
            self.groq_usage_today = {'requests': 0, 'tokens': 0}
            self.groq_last_reset = today

    def _log_request(self, provider: str, task_type: str, brand_id: str = None,
                     tokens_used: int = 0, latency_ms: int = 0,
                     success: bool = True, error_message: str = None) -> None:
        """
        Logs an LLM request to the database for monitoring.

        Parameters:
            provider: Provider used.
            task_type: Task category.
            brand_id: Optional brand context.
            tokens_used: Tokens consumed.
            latency_ms: Request latency.
            success: Whether the request succeeded.
            error_message: Error details if failed.
        """
        try:
            self.db.log_llm_request(
                provider=provider,
                task_type=task_type,
                brand_id=brand_id,
                tokens_used=tokens_used,
                latency_ms=latency_ms,
                success=success,
                error_message=error_message,
            )
        except Exception as e:
            logger.debug(f"Failed to log LLM request: {e}")

    def get_status(self) -> dict:
        """
        Returns provider health and usage status.

        Returns:
            Dictionary with ollama and groq health/usage information.
        """
        self._maybe_reset_daily_counters()
        return {
            'ollama': {
                'healthy': self.ollama_healthy,
                'model': self.ollama_model,
                'host': self.ollama_host,
            },
            'groq': {
                'healthy': self.groq_healthy,
                'configured': bool(self.groq_api_key),
                'requests_today': self.groq_usage_today['requests'],
                'tokens_today': self.groq_usage_today['tokens'],
                'limits': self.GROQ_DAILY_LIMITS,
            },
            'cached': {
                'available': self.cached_responses_dir.exists(),
                'files': [
                    f.name for f in self.cached_responses_dir.glob('*.json')
                ] if self.cached_responses_dir.exists() else [],
            }
        }

    def check_health(self) -> dict:
        """
        Actively checks provider health by making test calls.

        Returns:
            Dictionary with health check results for each provider.

        Side effects:
            Updates ollama_healthy and groq_healthy flags.
        """
        results = {}

        # Test Ollama
        try:
            import requests as req
            resp = req.get(f'{self.ollama_host}/api/tags', timeout=5)
            self.ollama_healthy = resp.status_code == 200
            results['ollama'] = {
                'healthy': self.ollama_healthy,
                'models': [m['name'] for m in resp.json().get('models', [])]
                if self.ollama_healthy else [],
            }
        except Exception as e:
            self.ollama_healthy = False
            results['ollama'] = {'healthy': False, 'error': str(e)}

        # Test Groq (lightweight — just check API key validity)
        if self.groq_api_key:
            try:
                import requests as req
                resp = req.get(
                    'https://api.groq.com/openai/v1/models',
                    headers={'Authorization': f'Bearer {self.groq_api_key}'},
                    timeout=10
                )
                self.groq_healthy = resp.status_code == 200
                results['groq'] = {'healthy': self.groq_healthy}
            except Exception as e:
                self.groq_healthy = False
                results['groq'] = {'healthy': False, 'error': str(e)}
        else:
            results['groq'] = {'healthy': False, 'error': 'No API key configured'}

        return results
