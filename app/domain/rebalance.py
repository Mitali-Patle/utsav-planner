"""Re-split a budget to a new total, keeping protected lines and explaining every move."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field

from app.domain.budget import validate_total_budget
from app.domain.errors import InfeasibleChangeError, NotFoundError
from app.domain.models import (
    BudgetCategory,
    CategoryId,
    DomainModel,
    EventPlan,
    Rupees,
    evolve,
)
from app.domain.money import format_inr, largest_remainder_split


class ChangeReason(StrEnum):
    """Why a category's allocation ended up where it did."""

    UNCHANGED = "unchanged"
    ADDED = "added"
    PROTECTED = "protected"
    GUEST_SCALED = "guest_scaled"
    CUT_FIRST = "cut_first"
    PROPORTIONAL = "proportional"
    FLOOR_REACHED = "floor_reached"
    SET = "set"


class Pin(DomainModel):
    """A fixed target amount for a category during a rebalance."""

    amount_inr: Rupees
    reason: ChangeReason


class CategoryChange(DomainModel):
    """Before and after allocation for one category, with the reason."""

    category_id: CategoryId
    before_inr: Rupees
    after_inr: Rupees
    reason: ChangeReason
    committed_inr: int = Field(ge=0)

    @property
    def delta_inr(self) -> int:
        """Change in allocation (negative for a cut)."""
        return self.after_inr - self.before_inr

    @property
    def overcommitted_by_inr(self) -> int:
        """How much existing spending exceeds the new allocation."""
        return max(0, self.committed_inr - self.after_inr)


class Rebalance(DomainModel):
    """Result of rebalancing a plan to a new total.

    When infeasible, `changes` is empty and `shortfall_inr` says exactly how many rupees
    are missing.
    """

    total_inr: int
    shortfall_inr: int = Field(ge=0)
    changes: tuple[CategoryChange, ...]

    @property
    def feasible(self) -> bool:
        """Whether the new total can be met."""
        return self.shortfall_inr == 0

    def apply(self, plan: EventPlan) -> EventPlan:
        """Apply the new allocations to the plan they were computed from.

        Args:
            plan: The plan passed to `rebalance`.

        Returns:
            A new plan with updated allocations and total.

        Raises:
            InfeasibleChangeError: If the rebalance is infeasible.
            ValueError: If the plan's categories differ from the rebalanced ones.
        """
        if not self.feasible:
            raise InfeasibleChangeError(
                f"This change needs {format_inr(self.shortfall_inr)} more than the budget allows.",
                code="budget_shortfall",
                shortfall_inr=self.shortfall_inr,
            )
        after = {change.category_id: change.after_inr for change in self.changes}
        if set(after) != {c.id for c in plan.categories}:
            raise ValueError("rebalance was computed for a different plan")
        categories = tuple(evolve(c, allocated_inr=after[c.id]) for c in plan.categories)
        return plan.with_changes(categories=categories, total_budget_inr=self.total_inr)


def rebalance(
    plan: EventPlan,
    *,
    total_inr: int,
    pins: Mapping[CategoryId, Pin] | None = None,
    cut_priority: Sequence[CategoryId] = (),
) -> Rebalance:
    """Reallocate the plan to a new total while respecting protections.

    Rules, in order:

    1. Protected categories keep their allocation exactly.
    2. Pinned categories (for example, scaled for a new guest count) get their pinned
       amount, raised to their protected-expense floor if needed.
    3. No other category drops below the total of its protected expenses.
    4. Extra money first covers categories whose spending exceeds their allocation (in
       proportion to each overspend), and the rest is shared in proportion to current
       allocations.
    5. Cuts come first from `cut_priority` categories (in order, down to their floor), then
       in proportion to what each remaining category can give.

    Runs in O(n log n) for n categories.

    Args:
        plan: Current plan.
        total_inr: New total budget.
        pins: Fixed amounts for some categories.
        cut_priority: Categories to cut first, most preferred first.

    Returns:
        Per-category before/after amounts with reasons, or the exact shortfall.

    Raises:
        InvalidInputError: If the new total is out of range.
        NotFoundError: If a pin or priority refers to an unknown category.
        InfeasibleChangeError: If extra money must be added but every category is fixed.
    """
    validate_total_budget(total_inr)
    pins = dict(pins or {})
    _require_known(plan, set(pins) | set(cut_priority))

    floors = {c.id: plan.protected_floor_inr(c.id) for c in plan.categories}
    fixed = _effective_pins(plan.categories, pins, floors)
    adjustable = [c for c in plan.categories if c.id not in fixed]
    base = {c.id: max(c.allocated_inr, floors[c.id]) for c in adjustable}
    need = total_inr - sum(p.amount_inr for p in fixed.values()) - sum(base.values())

    if need >= 0:
        after = _spread_increase(plan, adjustable, base, need)
        cut_first: set[CategoryId] = set()
    else:
        capacity = {cid: base[cid] - floors[cid] for cid in base}
        missing = -need - sum(capacity.values())
        if missing > 0:
            return Rebalance(total_inr=total_inr, shortfall_inr=missing, changes=())
        after, cut_first = _share_cut(adjustable, base, capacity, -need, cut_priority)

    outcome = _Allocation(fixed=fixed, after=after, floors=floors, cut_first=frozenset(cut_first))
    changes = tuple(outcome.change_for(plan, category) for category in plan.categories)
    return Rebalance(total_inr=total_inr, shortfall_inr=0, changes=changes)


def _require_known(plan: EventPlan, category_ids: set[CategoryId]) -> None:
    """Raise NotFoundError for the first category id the plan does not have."""
    unknown = category_ids - {c.id for c in plan.categories}
    if unknown:
        raise NotFoundError(f"No budget category '{sorted(unknown)[0]}' in this plan.")


def _spread_increase(
    plan: EventPlan,
    adjustable: Sequence[BudgetCategory],
    base: Mapping[CategoryId, int],
    need: int,
) -> dict[CategoryId, int]:
    """Share extra money, overspent lines first.

    Raises:
        InfeasibleChangeError: If there is money to add but no line can take it.
    """
    if need > 0 and not adjustable:
        raise InfeasibleChangeError(
            "Every category is fixed, so the extra budget has nowhere to go.",
            code="no_adjustable_category",
        )
    overspend = {c.id: max(0, plan.committed_inr(c.id) - base[c.id]) for c in adjustable}
    return _share_increase(adjustable, base, need, overspend)


@dataclass(frozen=True)
class _Allocation:
    """Where a rebalance put each line: pinned, shared out, held at its floor or cut first."""

    fixed: Mapping[CategoryId, Pin]
    after: Mapping[CategoryId, int]
    floors: Mapping[CategoryId, int]
    cut_first: frozenset[CategoryId]

    def change_for(self, plan: EventPlan, category: BudgetCategory) -> CategoryChange:
        """One line's before and after, with the reason it moved."""
        pin = self.fixed.get(category.id)
        new_amount = pin.amount_inr if pin else self.after[category.id]
        reason = (
            pin.reason
            if pin
            else _change_reason(
                category.allocated_inr,
                new_amount,
                floor_inr=self.floors[category.id],
                cut_first=category.id in self.cut_first,
            )
        )
        return CategoryChange(
            category_id=category.id,
            before_inr=category.allocated_inr,
            after_inr=new_amount,
            reason=reason,
            committed_inr=plan.committed_inr(category.id),
        )


def _effective_pins(
    categories: Sequence[BudgetCategory],
    pins: Mapping[CategoryId, Pin],
    floors: Mapping[CategoryId, int],
) -> dict[CategoryId, Pin]:
    effective: dict[CategoryId, Pin] = {}
    for category in categories:
        if category.protected:
            effective[category.id] = Pin(
                amount_inr=category.allocated_inr, reason=ChangeReason.PROTECTED
            )
        elif category.id in pins:
            pin = pins[category.id]
            floor = floors[category.id]
            effective[category.id] = (
                pin
                if pin.amount_inr >= floor
                else Pin(amount_inr=floor, reason=ChangeReason.FLOOR_REACHED)
            )
    return effective


def _share_increase(
    adjustable: Sequence[BudgetCategory],
    base: Mapping[CategoryId, int],
    increase: int,
    overspend: Mapping[CategoryId, int],
) -> dict[CategoryId, int]:
    if not adjustable:
        return {}
    ids = [c.id for c in adjustable]
    gaps = [overspend[cid] for cid in ids]
    if sum(gaps) >= increase:
        shares = largest_remainder_split(increase, gaps) if increase else [0] * len(ids)
        return {cid: base[cid] + share for cid, share in zip(ids, shares, strict=True)}
    filled = {cid: base[cid] + gap for cid, gap in zip(ids, gaps, strict=True)}
    shares = largest_remainder_split(increase - sum(gaps), _increase_weights(adjustable, base))
    return {c.id: filled[c.id] + share for c, share in zip(adjustable, shares, strict=True)}


def _increase_weights(
    adjustable: Sequence[BudgetCategory], base: Mapping[CategoryId, int]
) -> list[int]:
    """Share extra money by current amounts; if every line is at zero, by catalog weight.

    Equal shares are the last resort, when the catalog weights are zero too.
    """
    for weights in ([base[c.id] for c in adjustable], [c.weight for c in adjustable]):
        if sum(weights) > 0:
            return weights
    return [1] * len(adjustable)


def _share_cut(
    adjustable: Sequence[BudgetCategory],
    base: Mapping[CategoryId, int],
    capacity: Mapping[CategoryId, int],
    cut: int,
    cut_priority: Sequence[CategoryId],
) -> tuple[dict[CategoryId, int], set[CategoryId]]:
    cuts = dict.fromkeys(base, 0)
    remaining = cut
    cut_first: set[CategoryId] = set()
    for category_id in dict.fromkeys(cut_priority):
        if remaining == 0 or category_id not in capacity:
            continue
        take = min(capacity[category_id], remaining)
        if take > 0:
            cuts[category_id] = take
            cut_first.add(category_id)
            remaining -= take

    if remaining > 0:
        ids = [c.id for c in adjustable]
        spare = [capacity[cid] - cuts[cid] for cid in ids]
        for category_id, share in zip(ids, largest_remainder_split(remaining, spare), strict=True):
            cuts[category_id] += share

    return {cid: base[cid] - cuts[cid] for cid in base}, cut_first


def _change_reason(before: int, after: int, *, floor_inr: int, cut_first: bool) -> ChangeReason:
    """Explain an adjustable category's outcome.

    A category that ends exactly at a positive protected-expense floor (cut down to it,
    raised up to it, or held at it) is reported as `FLOOR_REACHED`.
    """
    at_floor = floor_inr > 0 and after == floor_inr
    if after < before:
        if at_floor:
            return ChangeReason.FLOOR_REACHED
        return ChangeReason.CUT_FIRST if cut_first else ChangeReason.PROPORTIONAL
    if at_floor:
        return ChangeReason.FLOOR_REACHED
    return ChangeReason.PROPORTIONAL if after > before else ChangeReason.UNCHANGED
