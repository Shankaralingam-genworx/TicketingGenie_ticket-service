"""Groq API client for LLM-powered comment enhancement with role-based prompting."""

import asyncio
import json
from typing import Optional

import httpx

from src.config.settings import settings
from src.observability.logging.logger import get_logger

logger = get_logger(__name__).bind(service="groq-client")


class GroqClient:
    """Async client for Groq API calls with role-specific enhancement prompts."""

    BASE_URL = "https://api.groq.com/openai/v1"
    TIMEOUT = 30  # seconds

    # Role-specific enhancement instructions
    ROLE_PROMPTS = {
        "customer": (
            "You are a professional assistant helping customers improve their support ticket comments. "
            "Your task is to enhance the customer's comment by:\n"
            "1. Fixing any grammar, spelling, or punctuation errors\n"
            "2. Improving clarity and professionalism while maintaining a courteous tone\n"
            "3. Maintaining the customer's original intent and emotion\n"
            "4. Ensuring it remains polite and easy to understand\n"
            "5. Keeping it concise and directly focused on the issue"
        ),
        "support_agent": (
            "You are an expert writing coach for support professionals. "
            "Your task is to enhance the support agent's internal comment by:\n"
            "1. Fixing any grammar, spelling, or punctuation errors\n"
            "2. Making it professional, concise, and clear for internal team reading\n"
            "3. Preserving technical accuracy and contextual information\n"
            "4. Using appropriate professional tone for peer communication\n"
            "5. Ensuring the comment is actionable and well-structured"
        ),
        "team_lead": (
            "You are an expert editor for team leadership communications. "
            "Your task is to enhance the team lead's comment by:\n"
            "1. Fixing any grammar, spelling, or punctuation errors\n"
            "2. Making it authoritative, polished, and impactful\n"
            "3. Ensuring strategic clarity and professional presentation\n"
            "4. Using appropriate leadership tone and perspective\n"
            "5. Maintaining professionalism and vision-alignment"
        ),
    }

    def __init__(self):
        self.api_key = settings.GROQ_API_KEY
        self.model = settings.GROQ_MODEL
        if not self.api_key:
            logger.warning("groq_api_key_missing")
        if not self.model:
            logger.warning("groq_model_not_configured")

    def _get_role_prompt(self, role: str) -> str:
        """Get the enhancement prompt based on user role.
        
        Args:
            role: User role (customer, support_agent, team_lead, etc.)
            
        Returns:
            Role-specific enhancement instruction prompt
        """
        role_lower = role.lower() if role else ""
        
        # Normalize role values
        if role_lower == "customer":
            return self.ROLE_PROMPTS["customer"]
        elif role_lower in ("support_agent", "agent", "support_agent"):
            return self.ROLE_PROMPTS["support_agent"]
        elif role_lower in ("team_lead", "teamlead", "team lead"):
            return self.ROLE_PROMPTS["team_lead"]
        else:
            # Default to customer-like enhancement for unknown roles
            logger.warning("unknown_role_for_enhancement", role=role)
            return self.ROLE_PROMPTS["customer"]

    async def enhance_comment(self, comment_text: str, role: str = "customer") -> Optional[str]:
        """
        Enhance a comment using Groq LLM with role-specific prompting.

        The enhancement varies based on user role:
        - Customer: Polite, clear, issue-focused
        - Support Agent: Professional, concise, internal communication
        - Team Lead: Authoritative, strategic, professional tone

        Args:
            comment_text: The raw comment text to enhance
            role: User role (customer, support_agent, team_lead)

        Returns:
            Enhanced comment text, or None if enhancement fails

        Raises:
            Exception: Log and return None on API errors
        """
        if not comment_text or not comment_text.strip():
            logger.warning("enhance_empty_comment")
            return None

        if not self.api_key or not self.model:
            logger.warning("enhance_missing_groq_config")
            return None

        role_instruction = self._get_role_prompt(role)
        prompt = f"""{role_instruction}

Original comment:
{comment_text}

Please provide ONLY the enhanced comment text, without any additional explanations or formatting."""

        try:
            logger.info(
                "enhance_comment_started",
                comment_length=len(comment_text),
                model=self.model,
                role=role,
            )

            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }

                payload = {
                    "model": self.model,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ],
                    "temperature": 0.3,  # Lower temperature for more consistent output
                    "max_tokens": len(comment_text) * 2,  # Allow 2x length for enhancements
                    "top_p": 0.9,
                }

                response = await client.post(
                    f"{self.BASE_URL}/chat/completions",
                    json=payload,
                    headers=headers,
                )

                if response.status_code != 200:
                    error_text = response.text
                    logger.warning(
                        "enhance_api_error",
                        status_code=response.status_code,
                        error=error_text,
                        role=role,
                    )
                    return None

                data = response.json()

                if (
                    not data.get("choices")
                    or len(data["choices"]) == 0
                    or not data["choices"][0].get("message")
                ):
                    logger.warning(
                        "enhance_invalid_response_format",
                        response=data,
                        role=role,
                    )
                    return None

                enhanced = data["choices"][0]["message"]["content"].strip()

                logger.info(
                    "enhance_comment_success",
                    original_length=len(comment_text),
                    enhanced_length=len(enhanced),
                    role=role,
                )

                return enhanced

        except asyncio.TimeoutError:
            logger.warning("enhance_api_timeout", timeout_seconds=self.TIMEOUT, role=role)
            return None
        except httpx.HTTPError as e:
            logger.warning("enhance_api_http_error", error=str(e), role=role)
            return None
        except json.JSONDecodeError as e:
            logger.warning("enhance_api_parse_error", error=str(e), role=role)
            return None
        except Exception as e:
            logger.exception(
                "enhance_api_unexpected_error",
                error=str(e),
                role=role,
            )
            return None


# Singleton instance
_groq_client: Optional[GroqClient] = None


def get_groq_client() -> GroqClient:
    """Get or create Groq client singleton."""
    global _groq_client
    if _groq_client is None:
        _groq_client = GroqClient()
    return _groq_client
