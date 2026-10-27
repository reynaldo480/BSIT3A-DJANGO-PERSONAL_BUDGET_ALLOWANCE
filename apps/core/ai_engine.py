"""
BudgetTrack — AI Budget Recommendation Engine
=============================================
Pure rule-based engine — no external APIs, no paid services.
All recommendations are derived from the student's own inputs and
calculated data.  Nothing is random or generic.

Public entry-point
------------------
    from apps.core.ai_engine import BudgetRecommendationEngine
    engine = BudgetRecommendationEngine(
        total_allowance=Decimal('500'),
        period='weekly',
        allocations={...},            # dict of category → Decimal
        spending_history={...},       # dict of category → Decimal (may be empty)
        savings_target=Decimal('50'), # may be 0
    )
    result = engine.analyze()
    # result is a dict with keys:
    #   status, sufficiency_pct, daily_limit, projected_savings,
    #   projected_shortage, tips, allocation_review,
    #   forecast_label, forecast_color, summary_line,
    #   recommended_splits
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# Thresholds (tunable)
# ---------------------------------------------------------------------------
CATEGORY_LABELS = {
    'food':            'Food',
    'transportation':  'Transportation',
    'school_expenses': 'School Expenses',
    'personal_needs':  'Personal Needs',
    'savings':         'Savings',
    'emergency_fund':  'Emergency Fund',
    'other':           'Other',
}

# Recommended % ranges per period (min%, ideal%, max%)
CATEGORY_TARGETS = {
    'daily': {
        'food':            (0.30, 0.40, 0.55),
        'transportation':  (0.20, 0.30, 0.40),
        'school_expenses': (0.05, 0.10, 0.20),
        'personal_needs':  (0.00, 0.05, 0.10),
        'savings':         (0.05, 0.10, 0.20),
        'emergency_fund':  (0.00, 0.05, 0.10),
        'other':           (0.00, 0.00, 0.10),
    },
    'weekly': {
        'food':            (0.25, 0.30, 0.45),
        'transportation':  (0.15, 0.25, 0.35),
        'school_expenses': (0.10, 0.15, 0.25),
        'personal_needs':  (0.03, 0.05, 0.10),
        'savings':         (0.10, 0.15, 0.25),
        'emergency_fund':  (0.05, 0.10, 0.15),
        'other':           (0.00, 0.00, 0.10),
    },
    'monthly': {
        'food':            (0.20, 0.30, 0.40),
        'transportation':  (0.10, 0.20, 0.30),
        'school_expenses': (0.10, 0.15, 0.25),
        'personal_needs':  (0.03, 0.05, 0.10),
        'savings':         (0.15, 0.20, 0.30),
        'emergency_fund':  (0.05, 0.10, 0.15),
        'other':           (0.00, 0.00, 0.10),
    },
}

PERIOD_DAYS = {'daily': 1, 'weekly': 7, 'monthly': 30}

TWO = Decimal('0.01')
ZERO = Decimal('0')


def _q(v: Decimal) -> Decimal:
    """Quantize to 2 decimal places."""
    return v.quantize(TWO, rounding=ROUND_HALF_UP)


class BudgetRecommendationEngine:
    """
    Analyzes a student's budget plan and produces actionable insights.
    All figures are Decimal for precision.
    """

    def __init__(
        self,
        total_allowance: Decimal,
        period: str,                          # 'daily' | 'weekly' | 'monthly'
        allocations: Dict[str, Decimal],      # what the student planned
        spending_history: Optional[Dict[str, Decimal]] = None,  # actual recorded
        savings_target: Decimal = ZERO,
    ):
        self.total = _q(total_allowance) if total_allowance > ZERO else ZERO
        self.period = period if period in PERIOD_DAYS else 'weekly'
        self.days = PERIOD_DAYS[self.period]
        self.alloc = {k: _q(Decimal(str(v))) for k, v in allocations.items()}
        self.history = {k: _q(Decimal(str(v))) for k, v in (spending_history or {}).items()}
        self.savings_target = _q(savings_target) if savings_target > ZERO else ZERO
        self._targets = CATEGORY_TARGETS[self.period]

    # ── Core calculations ──────────────────────────────────────────────────

    @property
    def total_allocated(self) -> Decimal:
        return _q(sum(self.alloc.values(), ZERO))

    @property
    def unallocated(self) -> Decimal:
        return _q(max(ZERO, self.total - self.total_allocated))

    @property
    def total_spent(self) -> Decimal:
        return _q(sum(self.history.values(), ZERO))

    @property
    def sufficiency_pct(self) -> int:
        """How much of the planned allocation the allowance covers (0-100+)."""
        if self.total_allocated == ZERO:
            return 100
        raw = (self.total / self.total_allocated * 100)
        return int(raw.quantize(Decimal('1'), rounding=ROUND_HALF_UP))

    @property
    def daily_limit(self) -> Decimal:
        """Recommended daily spend based on non-savings allocation."""
        spendable = self.total - self.alloc.get('savings', ZERO) - self.alloc.get('emergency_fund', ZERO)
        spendable = max(ZERO, spendable)
        return _q(spendable / Decimal(str(self.days)))

    @property
    def projected_savings(self) -> Decimal:
        """Planned savings + unallocated remainder."""
        return _q(self.alloc.get('savings', ZERO) + self.unallocated)

    @property
    def projected_shortage(self) -> Decimal:
        """How much short the allowance is vs. total planned."""
        return _q(max(ZERO, self.total_allocated - self.total))

    # ── Recommended splits ─────────────────────────────────────────────────

    def recommended_splits(self) -> Dict[str, Decimal]:
        """
        Build recommended allocations using ideal ratios for the period.
        Respects savings_target if provided.
        """
        if self.total == ZERO:
            return {k: ZERO for k in CATEGORY_LABELS}

        targets = self._targets
        result: Dict[str, Decimal] = {}
        assigned = ZERO

        # 1. Honour an explicit savings target first
        if self.savings_target > ZERO:
            result['savings'] = min(self.savings_target,
                                    _q(self.total * Decimal(str(targets['savings'][1]))))
            # Don't let savings exceed 30% of total
            result['savings'] = min(result['savings'], _q(self.total * Decimal('0.30')))
        else:
            result['savings'] = _q(self.total * Decimal(str(targets['savings'][1])))

        assigned += result['savings']

        # 2. Assign other categories proportionally (skip savings, handle last = other)
        cats_ordered = ['food', 'transportation', 'school_expenses',
                        'personal_needs', 'emergency_fund', 'other']
        for cat in cats_ordered:
            ideal_pct = Decimal(str(targets[cat][1]))
            v = _q(self.total * ideal_pct)
            result[cat] = v
            assigned += v

        # 3. Absorb rounding difference into 'other'
        diff = self.total - assigned
        result['other'] = max(ZERO, _q(result.get('other', ZERO) + diff))

        return result

    # ── Per-category review ────────────────────────────────────────────────

    def allocation_review(self) -> list:
        """
        For each category with a planned allocation, decide if it's
        under/over/ideal relative to recommended ranges.
        Returns list of dicts for template rendering.
        """
        if self.total == ZERO:
            return []

        rows = []
        for cat, label in CATEGORY_LABELS.items():
            planned = self.alloc.get(cat, ZERO)
            if planned == ZERO:
                continue

            actual_pct_dec = planned / self.total if self.total > ZERO else ZERO
            actual_pct = int((actual_pct_dec * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

            min_p, ideal_p, max_p = self._targets.get(cat, (0, 0, 1))
            ideal_pct_int = int(ideal_p * 100)

            if actual_pct_dec < Decimal(str(min_p)):
                verdict = 'low'
                tip = (
                    f'Your {label} budget (₱{planned:.2f}, {actual_pct}%) is below the '
                    f'recommended minimum of {int(min_p*100)}% for a {self.period} allowance. '
                    f'Consider increasing it to avoid shortfalls.'
                )
            elif actual_pct_dec > Decimal(str(max_p)):
                verdict = 'high'
                tip = (
                    f'Your {label} budget (₱{planned:.2f}, {actual_pct}%) is above the '
                    f'recommended maximum of {int(max_p*100)}%. '
                    f'Reducing it could free up ₱{_q(planned - self.total*Decimal(str(ideal_p))):.2f} '
                    f'for savings or other priorities.'
                )
            else:
                verdict = 'ideal'
                tip = (
                    f'{label} at {actual_pct}% (₱{planned:.2f}) is within the '
                    f'recommended range. Good planning.'
                )

            rows.append({
                'cat': cat,
                'label': label,
                'planned': planned,
                'actual_pct': actual_pct,
                'ideal_pct': ideal_pct_int,
                'verdict': verdict,
                'tip': tip,
            })
        return rows

    # ── Tips list ──────────────────────────────────────────────────────────

    def generate_tips(self) -> list:
        """
        Returns a prioritised list of actionable tips based on the student's
        actual numbers — never generic filler.
        """
        tips = []

        if self.total == ZERO:
            tips.append({
                'icon': 'fa-circle-exclamation',
                'color': 'text-warning',
                'text': 'You have no allowance recorded. Add your allowance to unlock all planning features.',
            })
            return tips

        # --- 1. Sufficiency ---
        shortage = self.projected_shortage
        if shortage > ZERO:
            tips.append({
                'icon': 'fa-triangle-exclamation',
                'color': 'text-danger',
                'text': (
                    f'Your planned allocations total ₱{self.total_allocated:.2f}, which exceeds '
                    f'your ₱{self.total:.2f} allowance by ₱{shortage:.2f}. '
                    f'Reduce at least one category to balance your plan.'
                ),
            })

        # --- 2. No savings planned ---
        if self.alloc.get('savings', ZERO) == ZERO:
            ideal_savings = _q(self.total * Decimal(str(self._targets['savings'][1])))
            tips.append({
                'icon': 'fa-piggy-bank',
                'color': 'text-warning',
                'text': (
                    f'You have ₱0 allocated for savings. Even a small amount helps. '
                    f'Based on your {self.period} allowance of ₱{self.total:.2f}, '
                    f'setting aside ₱{ideal_savings:.2f} ({int(self._targets["savings"][1]*100)}%) '
                    f'is a realistic starting point.'
                ),
            })

        # --- 3. No emergency fund ---
        if self.alloc.get('emergency_fund', ZERO) == ZERO and self.total >= Decimal('100'):
            ideal_ef = _q(self.total * Decimal(str(self._targets['emergency_fund'][1])))
            tips.append({
                'icon': 'fa-shield-halved',
                'color': 'text-info',
                'text': (
                    f'No emergency fund allocated. Setting aside ₱{ideal_ef:.2f} '
                    f'({int(self._targets["emergency_fund"][1]*100)}% of ₱{self.total:.2f}) '
                    f'creates a safety buffer for unexpected expenses.'
                ),
            })

        # --- 4. Overspending in a category (from history) ---
        for cat, label in CATEGORY_LABELS.items():
            planned = self.alloc.get(cat, ZERO)
            spent = self.history.get(cat, ZERO)
            if planned > ZERO and spent > planned:
                over = _q(spent - planned)
                tips.append({
                    'icon': 'fa-arrow-trend-up',
                    'color': 'text-danger',
                    'text': (
                        f'You have exceeded your {label} budget by ₱{over:.2f} '
                        f'(₱{spent:.2f} spent vs ₱{planned:.2f} planned). '
                        f'Consider adjusting your plan or reducing spending in this category.'
                    ),
                })

        # --- 5. Daily limit awareness ---
        if self.days > 1:
            tips.append({
                'icon': 'fa-calendar-day',
                'color': 'text-primary',
                'text': (
                    f'To stay within budget, aim to spend no more than '
                    f'₱{self.daily_limit:.2f} per day on everyday expenses '
                    f'(excluding savings and emergency fund).'
                ),
            })

        # --- 6. High food allocation ---
        food = self.alloc.get('food', ZERO)
        max_food_pct = Decimal(str(self._targets['food'][2]))
        if self.total > ZERO and food / self.total > max_food_pct:
            rec_food = _q(self.total * Decimal(str(self._targets['food'][1])))
            tips.append({
                'icon': 'fa-utensils',
                'color': 'text-warning',
                'text': (
                    f'Food is taking {int(food/self.total*100)}% of your allowance. '
                    f'The recommended ceiling is {int(max_food_pct*100)}%. '
                    f'Bringing it closer to ₱{rec_food:.2f} could free up '
                    f'₱{_q(food-rec_food):.2f} for savings.'
                ),
            })

        # --- 7. Healthy plan praise ---
        if not tips:
            tips.append({
                'icon': 'fa-circle-check',
                'color': 'text-success',
                'text': (
                    f'Your budget plan looks balanced! All categories are within '
                    f'recommended ranges and you have ₱{self.projected_savings:.2f} projected for savings.'
                ),
            })

        # --- 8. Unallocated remainder ---
        if self.unallocated > ZERO and shortage == ZERO:
            tips.append({
                'icon': 'fa-coins',
                'color': 'text-success',
                'text': (
                    f'You have ₱{self.unallocated:.2f} unallocated. '
                    f'Adding this to your savings or emergency fund will improve your financial resilience.'
                ),
            })

        return tips

    # ── Forecast labels ───────────────────────────────────────────────────

    def forecast_label(self) -> str:
        pct = self.sufficiency_pct
        if pct >= 105:
            return 'Excellent'
        if pct >= 100:
            return 'Sufficient'
        if pct >= 85:
            return 'Slightly Short'
        if pct >= 70:
            return 'Underfunded'
        return 'Insufficient'

    def forecast_color(self) -> str:
        pct = self.sufficiency_pct
        if pct >= 100:
            return 'success'
        if pct >= 85:
            return 'warning'
        return 'danger'

    def summary_line(self) -> str:
        pct = self.sufficiency_pct
        shortage = self.projected_shortage
        if shortage > ZERO:
            return (
                f'Your ₱{self.total:.2f} {self.period} allowance covers '
                f'{pct}% of your ₱{self.total_allocated:.2f} plan. '
                f'You are ₱{shortage:.2f} short — adjust allocations to balance.'
            )
        savings = self.projected_savings
        return (
            f'Your ₱{self.total:.2f} {self.period} allowance fully covers your plan '
            f'with ₱{savings:.2f} projected for savings.'
        )

    # ── Main entry point ──────────────────────────────────────────────────

    def analyze(self) -> dict:
        """
        Run all analyses and return a single result dict for views and templates.
        """
        review = self.allocation_review()
        tips = self.generate_tips()
        rec = self.recommended_splits()

        return {
            'status':              self.forecast_label(),
            'status_color':        self.forecast_color(),
            'sufficiency_pct':     self.sufficiency_pct,
            'total_allowance':     self.total,
            'total_allocated':     self.total_allocated,
            'unallocated':         self.unallocated,
            'daily_limit':         self.daily_limit,
            'projected_savings':   self.projected_savings,
            'projected_shortage':  self.projected_shortage,
            'summary_line':        self.summary_line(),
            'forecast_label':      self.forecast_label(),
            'forecast_color':      self.forecast_color(),
            'tips':                tips,
            'allocation_review':   review,
            'recommended_splits':  {k: float(v) for k, v in rec.items()},
        }
