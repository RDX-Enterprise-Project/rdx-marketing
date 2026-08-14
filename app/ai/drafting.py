"""AI drafting adapters.

AI is a writing assistant with no authority. It can improve prose. It cannot
decide what runs, on which platform, whether disclosure is allowed, whether
approval is required, or when anything publishes.

Three properties enforced here:

* **Output is copy, nothing else.** :class:`DraftResponse` carries text. There is
  no field on it for a classification, an approval, or a schedule, so a model
  cannot return one. :func:`apply_draft` writes only the variant body and
  records ``generated_by`` as ``ai:<model>``.
* **The prompt never carries authority.** ``prompt_policy`` in ``config/ai.yaml``
  keeps the disclosure class and the raw evidence out of the prompt: a model is
  not asked to reason about what may be disclosed, so it cannot be talked into
  widening it.
* **Budget fails closed.** Default is zero calls. When it is exhausted the
  deterministic template stands and the run continues.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from sqlalchemy.engine import Connection

from ..config import AppConfig
from ..content.item import ContentItem, PlatformVariant
from ..models import ai_usage

TASK_DRAFT = "draft"
TASK_HOOKS = "hooks"
TASK_ADAPT = "adapt"
TASK_SHORTEN = "shorten"
TASK_CAROUSEL = "carousel"

STATUS_OK = "OK"
STATUS_DISABLED = "DISABLED"
STATUS_BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
STATUS_ERROR = "ERROR"


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class DraftRequest:
    task: str
    platform: str
    core_message: str
    constraints: Dict[str, Any] = field(default_factory=dict)
    current_body: Optional[str] = None


@dataclass
class DraftResponse:
    """A model's entire permitted output surface: text and cost."""

    text: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class DraftingProvider(Protocol):
    name: str
    model: str

    def draft(self, request: DraftRequest) -> DraftResponse:
        ...


class NoProvider:
    """The default. The deterministic template stands."""

    name = "none"
    model = ""

    def draft(self, request: DraftRequest) -> DraftResponse:  # pragma: no cover
        raise RuntimeError("no drafting provider configured")


@dataclass
class Budget:
    max_calls: int = 0
    max_cost_usd: float = 0.0
    calls: int = 0
    spent: float = 0.0

    def can_spend(self, estimate: float = 0.0) -> bool:
        if self.calls >= self.max_calls:
            return False
        if self.max_cost_usd and (self.spent + estimate) > self.max_cost_usd:
            return False
        return True

    def record(self, cost: float) -> None:
        self.calls += 1
        self.spent += cost


class DraftingService:
    def __init__(
        self,
        config: AppConfig,
        providers: Optional[Dict[str, DraftingProvider]] = None,
    ) -> None:
        self._config = config
        self._providers = dict(providers or {})
        budget_cfg = config.ai.budget
        self.budget = Budget(
            max_calls=int(budget_cfg.get("max_calls_per_run", 0)),
            max_cost_usd=float(budget_cfg.get("max_cost_usd_per_run", 0.0)),
        )
        self.calls: List[str] = []

    @property
    def enabled(self) -> bool:
        return self._config.ai.enabled

    def model_for(self, task: str) -> str:
        return str(self._config.ai.task(task).get("model", ""))

    def improve(
        self,
        conn: Connection,
        item: ContentItem,
        variant: PlatformVariant,
        task: str,
        now: dt.datetime,
    ) -> Optional[str]:
        """Return improved copy, or ``None`` to keep the template output."""
        task_cfg = self._config.ai.task(task)
        provider_name = str(task_cfg.get("provider", "none"))

        if not self.enabled or provider_name in ("", "none"):
            self._log(conn, item, task, provider_name, "", now, STATUS_DISABLED)
            return None

        provider = self._providers.get(provider_name)
        if provider is None:
            self._log(
                conn, item, task, provider_name, "", now, STATUS_ERROR,
                "no provider registered for %r" % provider_name,
            )
            return None

        estimate = float(task_cfg.get("max_cost_usd", 0.0))
        if not self.budget.can_spend(estimate):
            self._log(
                conn, item, task, provider.name, provider.model, now, STATUS_BUDGET_EXCEEDED
            )
            return None

        request = self._build_request(item, variant, task)
        try:
            response = provider.draft(request)
        except Exception as exc:  # noqa: BLE001 - the template still stands
            self._log(
                conn, item, task, provider.name, provider.model, now, STATUS_ERROR, str(exc)
            )
            return None

        self.budget.record(response.cost_usd)
        self.calls.append(task)
        self._log(
            conn, item, task, response.provider, response.model, now, STATUS_OK,
            tokens_in=response.tokens_in, tokens_out=response.tokens_out,
            cost=response.cost_usd,
        )
        return response.text

    def _build_request(
        self, item: ContentItem, variant: PlatformVariant, task: str
    ) -> DraftRequest:
        policy = self._config.ai.prompt_policy
        constraints: Dict[str, Any] = {}
        if policy.get("send_platform_constraints", True):
            platform_cfg = self._config.platforms.platform(variant.platform)
            constraints = {
                "tone": platform_cfg.get("tone"),
                "target_words": (platform_cfg.get("body", {}) or {}).get("target_words"),
                "hard_max_chars": (platform_cfg.get("body", {}) or {}).get("hard_max_chars"),
                "no_em_dashes": True,
            }
        # Disclosure class and raw evidence are deliberately absent: a model is
        # never asked to reason about what RDX may disclose.
        return DraftRequest(
            task=task,
            platform=variant.platform,
            core_message=item.core_message if policy.get("send_core_message", True) else "",
            constraints=constraints,
            current_body=variant.body,
        )

    def _log(
        self,
        conn: Connection,
        item: ContentItem,
        task: str,
        provider: str,
        model: str,
        now: dt.datetime,
        status: str,
        error: Optional[str] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost: float = 0.0,
    ) -> None:
        conn.execute(
            ai_usage.insert().values(
                content_id=item.content_id,
                created_at=now,
                task=task,
                provider=provider or "none",
                model=model or "",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                status=status,
                error_message=error,
            )
        )


def apply_draft(variant: PlatformVariant, text: str, model_label: str) -> PlatformVariant:
    """Replace only the body, and record that a model wrote it.

    Nothing else on the variant is touched, and nothing on the content item is
    reachable from here. A model cannot grant itself publication permission
    because there is no code path from its output to an approval.
    """
    variant.body = text
    variant.generated_by = "ai:%s" % model_label
    return variant
