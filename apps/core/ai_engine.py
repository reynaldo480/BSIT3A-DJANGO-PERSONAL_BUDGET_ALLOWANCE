"""
BudgetTrack — Budget Recommendation Engine + Conversational Planner
===================================================================
Pure rule-based engine — no external APIs, no paid services.
All advice is derived from the student's own recorded data.

Public classes
--------------
BudgetRecommendationEngine   – analysis & tips for the dashboard
ConversationalPlanner        – interactive budget coaching for Smart Budget Planner
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
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

CATEGORY_ICONS = {
    'food': 'fa-utensils',
    'transportation': 'fa-bus',
    'school_expenses': 'fa-book',
    'personal_needs': 'fa-user',
    'savings': 'fa-piggy-bank',
    'emergency_fund': 'fa-shield-halved',
    'other': 'fa-box',
}

CATEGORY_COLORS = {
    'food':            '#f59e0b',
    'transportation':  '#3b82f6',
    'school_expenses': '#8b5cf6',
    'personal_needs':  '#ec4899',
    'savings':         '#10b981',
    'emergency_fund':  '#06b6d4',
    'other':           '#6b7280',
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
PERIOD_LABELS = {'daily': 'day', 'weekly': 'week', 'monthly': 'month'}

TWO  = Decimal('0.01')
ZERO = Decimal('0')


def _q(v: Decimal) -> Decimal:
    """Round to 2 decimal places."""
    return v.quantize(TWO, rounding=ROUND_HALF_UP)


# ===========================================================================
# BudgetRecommendationEngine  (used by Dashboard)
# ===========================================================================

class BudgetRecommendationEngine:
    """
    Analyses a student's budget plan and produces actionable insights
    for the dashboard.  All figures are Decimal for precision.
    """

    def __init__(
        self,
        total_allowance: Decimal,
        period: str,
        allocations: Dict[str, Decimal],
        spending_history: Optional[Dict[str, Decimal]] = None,
        savings_target: Decimal = ZERO,
    ):
        self.total    = _q(total_allowance) if total_allowance > ZERO else ZERO
        self.period   = period if period in PERIOD_DAYS else 'weekly'
        self.days     = PERIOD_DAYS[self.period]
        self.alloc    = {k: _q(Decimal(str(v))) for k, v in allocations.items()}
        self.history  = {k: _q(Decimal(str(v))) for k, v in (spending_history or {}).items()}
        self.savings_target = _q(savings_target) if savings_target > ZERO else ZERO
        self._targets = CATEGORY_TARGETS[self.period]

    # ── Core properties ────────────────────────────────────────────────────

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
        if self.total_allocated == ZERO:
            return 100
        return int((self.total / self.total_allocated * 100).quantize(
            Decimal('1'), rounding=ROUND_HALF_UP))

    @property
    def daily_limit(self) -> Decimal:
        spendable = max(ZERO, self.total
                        - self.alloc.get('savings', ZERO)
                        - self.alloc.get('emergency_fund', ZERO))
        return _q(spendable / Decimal(str(self.days)))

    @property
    def projected_savings(self) -> Decimal:
        return _q(self.alloc.get('savings', ZERO) + self.unallocated)

    @property
    def projected_shortage(self) -> Decimal:
        return _q(max(ZERO, self.total_allocated - self.total))

    # ── Labels ─────────────────────────────────────────────────────────────

    def forecast_label(self) -> str:
        p = self.sufficiency_pct
        if p >= 105: return 'Excellent'
        if p >= 100: return 'Sufficient'
        if p >= 85:  return 'Slightly Short'
        if p >= 70:  return 'Underfunded'
        return 'Insufficient'

    def forecast_color(self) -> str:
        p = self.sufficiency_pct
        if p >= 100: return 'success'
        if p >= 85:  return 'warning'
        return 'danger'

    def summary_line(self) -> str:
        shortage = self.projected_shortage
        if shortage > ZERO:
            return (
                f'Your ₱{self.total:.2f} {self.period} allowance covers '
                f'{self.sufficiency_pct}% of your ₱{self.total_allocated:.2f} plan. '
                f'You are ₱{shortage:.2f} short — adjust allocations to balance.'
            )
        return (
            f'Your ₱{self.total:.2f} {self.period} allowance fully covers your plan '
            f'with ₱{self.projected_savings:.2f} projected for savings.'
        )

    # ── Recommended splits ─────────────────────────────────────────────────

    def recommended_splits(self) -> Dict[str, Decimal]:
        if self.total == ZERO:
            return {k: ZERO for k in CATEGORY_LABELS}
        targets  = self._targets
        result: Dict[str, Decimal] = {}
        assigned = ZERO

        # Respect an explicit savings target
        if self.savings_target > ZERO:
            result['savings'] = min(
                self.savings_target,
                min(_q(self.total * Decimal(str(targets['savings'][1]))),
                    _q(self.total * Decimal('0.30')))
            )
        else:
            result['savings'] = _q(self.total * Decimal(str(targets['savings'][1])))
        assigned += result['savings']

        for cat in ['food', 'transportation', 'school_expenses',
                    'personal_needs', 'emergency_fund', 'other']:
            v = _q(self.total * Decimal(str(targets[cat][1])))
            result[cat] = v
            assigned += v

        # Absorb rounding remainder into 'other'
        result['other'] = max(ZERO, _q(result.get('other', ZERO) + (self.total - assigned)))
        return result

    # ── Allocation review ──────────────────────────────────────────────────

    def allocation_review(self) -> list:
        if self.total == ZERO:
            return []
        rows = []
        for cat, label in CATEGORY_LABELS.items():
            planned = self.alloc.get(cat, ZERO)
            if planned == ZERO:
                continue
            pct_dec  = planned / self.total if self.total > ZERO else ZERO
            pct_int  = int((pct_dec * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            min_p, ideal_p, max_p = self._targets.get(cat, (0, 0, 1))
            if pct_dec < Decimal(str(min_p)):
                verdict = 'low'
                tip = (f'Your {label} budget ({pct_int}%, ₱{planned:.2f}) is below '
                       f'the recommended minimum of {int(min_p*100)}% for a {self.period} allowance.')
            elif pct_dec > Decimal(str(max_p)):
                verdict = 'high'
                savings_gain = _q(planned - self.total * Decimal(str(ideal_p)))
                tip = (f'Your {label} budget ({pct_int}%, ₱{planned:.2f}) exceeds '
                       f'the recommended maximum of {int(max_p*100)}%. '
                       f'Reducing it by ₱{savings_gain:.2f} could boost your savings.')
            else:
                verdict = 'ideal'
                tip = (f'{label} at {pct_int}% (₱{planned:.2f}) is within the '
                       f'recommended range — good planning.')
            rows.append({'cat': cat, 'label': label, 'planned': planned,
                         'actual_pct': pct_int, 'ideal_pct': int(ideal_p * 100),
                         'verdict': verdict, 'tip': tip})
        return rows

    # ── Tips ───────────────────────────────────────────────────────────────

    def generate_tips(self) -> list:
        tips = []
        if self.total == ZERO:
            tips.append({'icon': 'fa-circle-exclamation', 'color': 'text-warning',
                         'text': 'Add your allowance to unlock all planning features.'})
            return tips

        shortage = self.projected_shortage
        if shortage > ZERO:
            tips.append({'icon': 'fa-triangle-exclamation', 'color': 'text-danger',
                         'text': (f'Your allocations (₱{self.total_allocated:.2f}) exceed your '
                                  f'₱{self.total:.2f} allowance by ₱{shortage:.2f}. '
                                  f'Reduce at least one category to balance your plan.')})

        if self.alloc.get('savings', ZERO) == ZERO:
            ideal = _q(self.total * Decimal(str(self._targets['savings'][1])))
            tips.append({'icon': 'fa-piggy-bank', 'color': 'text-warning',
                         'text': (f'No savings allocated. Setting aside ₱{ideal:.2f} '
                                  f'({int(self._targets["savings"][1]*100)}% of ₱{self.total:.2f}) '
                                  f'is a realistic starting point.')})

        if self.alloc.get('emergency_fund', ZERO) == ZERO and self.total >= Decimal('100'):
            ideal_ef = _q(self.total * Decimal(str(self._targets['emergency_fund'][1])))
            tips.append({'icon': 'fa-shield-halved', 'color': 'text-info',
                         'text': (f'No emergency fund allocated. ₱{ideal_ef:.2f} '
                                  f'({int(self._targets["emergency_fund"][1]*100)}%) would create '
                                  f'a buffer for unexpected expenses.')})

        for cat, label in CATEGORY_LABELS.items():
            planned = self.alloc.get(cat, ZERO)
            spent   = self.history.get(cat, ZERO)
            if planned > ZERO and spent > planned:
                tips.append({'icon': 'fa-arrow-trend-up', 'color': 'text-danger',
                             'text': (f'You exceeded your {label} budget by '
                                      f'₱{_q(spent-planned):.2f} '
                                      f'(₱{spent:.2f} spent vs ₱{planned:.2f} planned).')})

        if self.days > 1:
            tips.append({'icon': 'fa-calendar-day', 'color': 'text-primary',
                         'text': (f'Aim to spend no more than ₱{self.daily_limit:.2f}/day on '
                                  f'everyday expenses (excluding savings and emergency fund).')})

        food      = self.alloc.get('food', ZERO)
        max_food  = Decimal(str(self._targets['food'][2]))
        if self.total > ZERO and food > ZERO and food / self.total > max_food:
            rec = _q(self.total * Decimal(str(self._targets['food'][1])))
            tips.append({'icon': 'fa-utensils', 'color': 'text-warning',
                         'text': (f'Food is taking {int(food/self.total*100)}% of your allowance '
                                  f'(max recommended: {int(max_food*100)}%). '
                                  f'Trimming to ₱{rec:.2f} would free up ₱{_q(food-rec):.2f}.')})

        if not tips:
            tips.append({'icon': 'fa-circle-check', 'color': 'text-success',
                         'text': (f'Your budget plan looks balanced! All categories are within '
                                  f'recommended ranges with ₱{self.projected_savings:.2f} '
                                  f'projected for savings.')})

        if self.unallocated > ZERO and shortage == ZERO:
            tips.append({'icon': 'fa-coins', 'color': 'text-success',
                         'text': (f'You have ₱{self.unallocated:.2f} unallocated. Consider '
                                  f'directing it to savings or your emergency fund.')})

        return tips

    # ── Main entry point ───────────────────────────────────────────────────

    def analyze(self) -> dict:
        review = self.allocation_review()
        tips   = self.generate_tips()
        rec    = self.recommended_splits()
        return {
            'status':             self.forecast_label(),
            'status_color':       self.forecast_color(),
            'sufficiency_pct':    self.sufficiency_pct,
            'total_allowance':    self.total,
            'total_allocated':    self.total_allocated,
            'unallocated':        self.unallocated,
            'daily_limit':        self.daily_limit,
            'projected_savings':  self.projected_savings,
            'projected_shortage': self.projected_shortage,
            'summary_line':       self.summary_line(),
            'forecast_label':     self.forecast_label(),
            'forecast_color':     self.forecast_color(),
            'tips':               tips,
            'allocation_review':  review,
            'recommended_splits': {k: float(v) for k, v in rec.items()},
        }


# ===========================================================================
# ConversationalPlanner  (used by Smart Budget Planner)
# ===========================================================================

class ConversationalPlanner:
    """
    Generates natural-language coaching messages and personalised
    recommendations for the Smart Budget Planner.

    All messages are built from actual user data — nothing is hardcoded
    or random.  The class is stateless; call respond() with an intent key.

    Parameters
    ----------
    total_allowance   : total money available (Decimal)
    period            : 'daily' | 'weekly' | 'monthly'
    remaining_balance : current available balance (Decimal)
    allocations       : dict of category → planned Decimal amount
    spending_history  : dict of category → actual spent Decimal amount
    savings_goals     : list of dicts with keys: name, target, current, deadline_days
    """

    INTENT_LABELS = {
        'help_budget':     'Help me budget my allowance',
        'daily_limit':     'How much can I spend today?',
        'save_more':       'How can I save more?',
        'afford_goal':     'Can I afford my goal?',
        'review_spending': 'Review my spending',
    }

    def __init__(
        self,
        total_allowance: Decimal,
        period: str,
        remaining_balance: Decimal,
        allocations: Optional[Dict[str, Decimal]] = None,
        spending_history: Optional[Dict[str, Decimal]] = None,
        savings_goals: Optional[List[dict]] = None,
    ):
        self.total     = _q(max(ZERO, total_allowance))
        self.period    = period if period in PERIOD_DAYS else 'weekly'
        self.days      = PERIOD_DAYS[self.period]
        self.period_lbl = PERIOD_LABELS[self.period]
        self.balance   = _q(remaining_balance)
        self.alloc     = {k: _q(Decimal(str(v))) for k, v in (allocations or {}).items()}
        self.history   = {k: _q(Decimal(str(v))) for k, v in (spending_history or {}).items()}
        self.goals     = savings_goals or []
        self._targets  = CATEGORY_TARGETS[self.period]

    # ── Derived helpers ────────────────────────────────────────────────────

    @property
    def total_allocated(self) -> Decimal:
        return _q(sum(self.alloc.values(), ZERO))

    @property
    def total_spent(self) -> Decimal:
        return _q(sum(self.history.values(), ZERO))

    @property
    def is_over_budget(self) -> bool:
        return self.total_allocated > ZERO and self.total_spent > self.total_allocated

    @property
    def daily_safe_limit(self) -> Decimal:
        spendable = max(ZERO, self.total
                        - self.alloc.get('savings', ZERO)
                        - self.alloc.get('emergency_fund', ZERO))
        return _q(spendable / Decimal(str(self.days)))

    def _highest_spending_cat(self) -> Optional[str]:
        if not self.history:
            return None
        return max(self.history, key=lambda k: self.history[k])

    def _over_budget_cats(self) -> List[str]:
        cats = []
        for cat in CATEGORY_LABELS:
            planned = self.alloc.get(cat, ZERO)
            spent   = self.history.get(cat, ZERO)
            if planned > ZERO and spent > planned:
                cats.append(cat)
        return cats

    def _approaching_limit_cats(self) -> List[str]:
        cats = []
        for cat in CATEGORY_LABELS:
            planned = self.alloc.get(cat, ZERO)
            spent   = self.history.get(cat, ZERO)
            if planned > ZERO and ZERO < spent <= planned:
                pct = spent / planned
                if pct >= Decimal('0.80'):
                    cats.append(cat)
        return cats

    def _unused_cats(self) -> List[str]:
        cats = []
        for cat in CATEGORY_LABELS:
            planned = self.alloc.get(cat, ZERO)
            spent   = self.history.get(cat, ZERO)
            if planned > ZERO and spent == ZERO:
                cats.append(cat)
        return cats

    # ── Intent handlers ────────────────────────────────────────────────────

    def _intent_help_budget(self) -> dict:
        """General 'help me budget' coaching."""
        if self.total == ZERO:
            return {
                'message': (
                    "Let's start by adding your allowance! Once I know how much you have, "
                    "I can suggest how to divide it between food, transportation, school, savings, "
                    "and an emergency buffer."
                ),
                'follow_up': "How much allowance do you receive per day, week, or month?",
                'suggestions': [],
            }

        period_lbl = self.period_lbl
        total = self.total

        if self.total_allocated == ZERO:
            # No plan yet — give a friendly intro + quick split
            splits = self._smart_split()
            sugg = [{'cat': cat, 'label': CATEGORY_LABELS[cat],
                     'amount': float(splits[cat]),
                     'pct': int(splits[cat] / total * 100) if total > ZERO else 0,
                     'reason': self._reason(cat, splits[cat], total),
                     'icon': CATEGORY_ICONS[cat], 'color': CATEGORY_COLORS[cat]}
                    for cat in CATEGORY_LABELS if splits[cat] > ZERO]
            return {
                'message': (
                    f"Let's make your ₱{total:.2f} last the whole {period_lbl}! "
                    f"I'll help you set aside money for your essential needs first — "
                    f"food and transportation — then school expenses, and finally save "
                    f"a portion for your goals and emergencies."
                ),
                'follow_up': (
                    "Here's a suggested allocation based on recommended student budget ratios. "
                    "You can adjust any amount before saving."
                ),
                'suggestions': sugg,
            }

        # Plan exists — give a status summary
        over  = self._over_budget_cats()
        near  = self._approaching_limit_cats()
        unused = self._unused_cats()
        remaining = self.balance

        parts = [f"Your ₱{total:.2f} {period_lbl} budget is set. "]
        if over:
            labels = ', '.join(CATEGORY_LABELS[c] for c in over)
            parts.append(f"You've exceeded your budget in {labels}. ")
        if near:
            labels = ', '.join(CATEGORY_LABELS[c] for c in near)
            parts.append(f"{labels} {'is' if len(near)==1 else 'are'} close to the limit. ")
        if remaining > ZERO and not over:
            parts.append(f"You have ₱{remaining:.2f} available right now. ")
        elif remaining <= ZERO:
            parts.append("Your available balance is fully committed. ")

        follow_up = None
        if over:
            follow_up = "Would you like me to suggest where to cut back?"
        elif near:
            follow_up = "Want me to check which categories still have room?"
        elif unused:
            labels = ', '.join(CATEGORY_LABELS[c] for c in unused[:2])
            follow_up = f"You haven't spent anything on {labels} yet — is that intentional?"

        return {'message': ''.join(parts).strip(), 'follow_up': follow_up, 'suggestions': []}

    def _intent_daily_limit(self) -> dict:
        """How much can I spend today?"""
        daily = self.daily_safe_limit

        if self.total == ZERO:
            return {
                'message': "Add your allowance first so I can calculate a safe daily limit for you.",
                'follow_up': None, 'suggestions': [],
            }

        if self.period == 'daily':
            spendable = max(ZERO, self.total
                            - self.alloc.get('savings', ZERO)
                            - self.alloc.get('emergency_fund', ZERO))
            msg = (f"For today's ₱{self.total:.2f} allowance, you can spend up to "
                   f"₱{spendable:.2f} on everyday expenses after setting aside "
                   f"₱{self.alloc.get('savings', ZERO):.2f} for savings and "
                   f"₱{self.alloc.get('emergency_fund', ZERO):.2f} for emergencies.")
        else:
            msg = (f"Based on your ₱{self.total:.2f} {self.period_lbl} allowance, "
                   f"your safe daily spending limit is ₱{daily:.2f}. "
                   f"This keeps you within budget while protecting your savings "
                   f"and emergency fund.")

        # Factor in remaining balance
        if self.balance < daily and self.balance > ZERO:
            msg += (f" However, your current available balance is ₱{self.balance:.2f}, "
                    f"so today's actual limit is closer to ₱{self.balance:.2f}.")
        elif self.balance <= ZERO:
            msg += " Your available balance is currently ₱0 or below — be careful with new expenses."

        # Warn about over-budget categories
        over = self._over_budget_cats()
        follow_up = None
        if over:
            labels = ', '.join(CATEGORY_LABELS[c] for c in over)
            follow_up = f"Note: You've already exceeded your budget for {labels}."

        return {'message': msg, 'follow_up': follow_up, 'suggestions': []}

    def _intent_save_more(self) -> dict:
        """How can I save more?"""
        current_savings = self.alloc.get('savings', ZERO)
        ideal_savings   = _q(self.total * Decimal(str(self._targets['savings'][1])))

        if self.total == ZERO:
            return {
                'message': "Add your allowance so I can suggest a realistic savings target for you.",
                'follow_up': None, 'suggestions': [],
            }

        # Find non-essential categories that are over the ideal ratio
        high_cats = []
        for cat in ['personal_needs', 'other', 'food']:
            planned = self.alloc.get(cat, ZERO)
            _, ideal_p, max_p = self._targets.get(cat, (0, 0, 1))
            ideal_amt = _q(self.total * Decimal(str(ideal_p)))
            if planned > ideal_amt:
                surplus = _q(planned - ideal_amt)
                high_cats.append((CATEGORY_LABELS[cat], planned, ideal_amt, surplus))

        parts = []
        if current_savings == ZERO:
            parts.append(
                f"You currently have no savings allocated. "
                f"Based on your ₱{self.total:.2f} {self.period_lbl} allowance, "
                f"I'd recommend setting aside ₱{ideal_savings:.2f} "
                f"({int(self._targets['savings'][1]*100)}%) for savings."
            )
        elif current_savings < ideal_savings:
            gap = _q(ideal_savings - current_savings)
            parts.append(
                f"You're saving ₱{current_savings:.2f} per {self.period_lbl}. "
                f"Increasing that by ₱{gap:.2f} would bring you to the "
                f"recommended {int(self._targets['savings'][1]*100)}% savings rate."
            )
        else:
            parts.append(
                f"Great — you're already saving ₱{current_savings:.2f} per {self.period_lbl}. "
                f"That's {int(current_savings/self.total*100) if self.total>ZERO else 0}% of your allowance."
            )

        sugg = []
        if high_cats:
            parts.append(" Here's where you could trim:")
            for label, planned, ideal_amt, surplus in high_cats[:3]:
                parts.append(f" Reducing {label} from ₱{planned:.2f} to ₱{ideal_amt:.2f} "
                              f"frees up ₱{surplus:.2f}.")
                sugg.append({
                    'text': f"Reduce {label} by ₱{surplus:.2f} (from ₱{planned:.2f} to ₱{ideal_amt:.2f})",
                    'saving': float(surplus),
                })

        if self.balance > ZERO:
            parts.append(
                f" You also have ₱{self.balance:.2f} unspent. "
                f"Consider directing some of that toward savings or your emergency fund."
            )

        follow_up = None
        if self.goals:
            g = self.goals[0]
            follow_up = (f"You have a goal: {g['name']} (₱{g['current']:.2f}/₱{g['target']:.2f}). "
                         f"Would you like to know if you're on track?")

        return {'message': ' '.join(parts).strip(), 'follow_up': follow_up, 'suggestions': sugg}

    def _intent_afford_goal(self) -> dict:
        """Can I afford my savings goal?"""
        if not self.goals:
            return {
                'message': (
                    "You don't have any savings goals set up yet. "
                    "Create one in the Goals section and I'll help you figure out "
                    "how to reach it within your allowance."
                ),
                'follow_up': None, 'suggestions': [],
            }

        if self.total == ZERO:
            return {
                'message': "Add your allowance so I can check whether your savings goals are achievable.",
                'follow_up': None, 'suggestions': [],
            }

        results = []
        for g in self.goals[:3]:  # limit to first 3 goals
            name    = g.get('name', 'Goal')
            target  = Decimal(str(g.get('target', 0)))
            current = Decimal(str(g.get('current', 0)))
            remaining_goal = max(ZERO, target - current)
            deadline_days  = g.get('deadline_days')  # None if no deadline

            savings_per_period = self.alloc.get('savings', ZERO)

            if remaining_goal == ZERO:
                results.append(f"{name} is already completed!")
                continue

            if savings_per_period <= ZERO:
                results.append(
                    f"For {name} (₱{remaining_goal:.2f} remaining): "
                    f"you have no savings allocated yet. "
                    f"Allocating even ₱{_q(self.total*Decimal('0.10')):.2f}/period would get you there."
                )
                continue

            periods_needed = int((remaining_goal / savings_per_period).quantize(
                Decimal('1'), rounding=ROUND_HALF_UP))
            days_needed    = periods_needed * self.days

            if deadline_days is not None and deadline_days > 0:
                periods_available = max(1, deadline_days // self.days)
                needed_per_period = _q(remaining_goal / Decimal(str(periods_available)))
                if savings_per_period >= needed_per_period:
                    results.append(
                        f"{name}: Yes, you can reach this goal. "
                        f"You need ₱{needed_per_period:.2f}/{self.period_lbl} and you're saving "
                        f"₱{savings_per_period:.2f}/{self.period_lbl} — you're on track!"
                    )
                else:
                    shortfall = _q(needed_per_period - savings_per_period)
                    results.append(
                        f"{name}: You need ₱{needed_per_period:.2f}/{self.period_lbl} to hit your deadline, "
                        f"but you're only saving ₱{savings_per_period:.2f}. "
                        f"Increase savings by ₱{shortfall:.2f}/{self.period_lbl} to stay on track."
                    )
            else:
                results.append(
                    f"{name}: At ₱{savings_per_period:.2f}/{self.period_lbl}, "
                    f"you'll reach ₱{target:.2f} in about {periods_needed} {self.period_lbl}s "
                    f"({days_needed} days) — roughly "
                    f"{'less than 1 month' if days_needed < 30 else f'{days_needed//30} month(s)'}."
                )

        message = ' '.join(results)
        follow_up = None
        if len(self.goals) > 3:
            follow_up = f"You have {len(self.goals)} goals total. The above covers your first 3."

        return {'message': message, 'follow_up': follow_up, 'suggestions': []}

    def _intent_review_spending(self) -> dict:
        """Review my spending."""
        if self.total_spent == ZERO and self.total_allocated == ZERO:
            return {
                'message': (
                    "No spending or budget plan recorded yet. "
                    "Once you add expenses and a budget plan, I'll give you a full spending review."
                ),
                'follow_up': None, 'suggestions': [],
            }

        parts = []
        if self.is_over_budget:
            parts.append(
                f"Your recorded expenses (₱{self.total_spent:.2f}) are higher than your "
                f"planned budget (₱{self.total_allocated:.2f}). "
                f"Here's which categories used the most:"
            )
        elif self.total_spent > ZERO:
            pct_used = int(self.total_spent / self.total * 100) if self.total > ZERO else 0
            parts.append(
                f"You've used ₱{self.total_spent:.2f} of your ₱{self.total:.2f} "
                f"{self.period_lbl} allowance ({pct_used}%). "
            )
            if self.balance > ZERO:
                parts.append(f"You have ₱{self.balance:.2f} remaining.")

        breakdown = []
        for cat, label in CATEGORY_LABELS.items():
            planned = self.alloc.get(cat, ZERO)
            spent   = self.history.get(cat, ZERO)
            if spent == ZERO and planned == ZERO:
                continue
            pct    = int(spent / planned * 100) if planned > ZERO else None
            status = 'over' if (planned > ZERO and spent > planned) else \
                     'near' if (planned > ZERO and spent >= planned * Decimal('0.8')) else 'ok'
            breakdown.append({
                'cat': cat, 'label': label,
                'planned': float(planned), 'spent': float(spent),
                'pct': pct, 'status': status,
                'icon': CATEGORY_ICONS[cat], 'color': CATEGORY_COLORS[cat],
                'remaining': float(max(ZERO, planned - spent)),
            })

        # Sort: over first, then near, then ok
        order = {'over': 0, 'near': 1, 'ok': 2}
        breakdown.sort(key=lambda r: order[r['status']])

        # Recommendations
        sugg = []
        over_cats  = [b for b in breakdown if b['status'] == 'over']
        ok_cats    = [b for b in breakdown if b['status'] == 'ok' and b['remaining'] > 0]
        if over_cats:
            for b in over_cats[:2]:
                over_by = round(b['spent'] - b['planned'], 2)
                sugg.append({'text': f"Cut back on {b['label']} — you're ₱{over_by:.2f} over planned."})
        if ok_cats and self.is_over_budget:
            b = ok_cats[0]
            sugg.append({'text': f"{b['label']} still has ₱{b['remaining']:.2f} remaining — that cushion can help."})

        return {
            'message': ''.join(parts).strip(),
            'follow_up': "Select a category above for details." if breakdown else None,
            'suggestions': sugg,
            'breakdown': breakdown,
        }

    # ── Smart split for "no plan" state ────────────────────────────────────

    def _smart_split(self) -> Dict[str, Decimal]:
        """
        Return suggested allocations adapted to spending history and goals.
        Total is guaranteed to equal self.total exactly (rounding absorbed by 'other').
        """
        targets  = self._targets
        total    = self.total
        if total == ZERO:
            return {k: ZERO for k in CATEGORY_LABELS}

        result: Dict[str, Decimal] = {}
        assigned = ZERO

        # ── Savings: goal-aware ────────────────────────────────────────────
        savings_ideal_pct = Decimal(str(targets['savings'][1]))
        max_savings_pct   = Decimal('0.25')   # never exceed 25% for savings alone

        if self.goals:
            active = [g for g in self.goals
                      if Decimal(str(g.get('current', 0))) < Decimal(str(g.get('target', 0)))]
            if active:
                # Bump savings % by 5pp, but keep total ≤ 25%
                savings_ideal_pct = min(max_savings_pct,
                                        savings_ideal_pct + Decimal('0.05'))

        result['savings'] = _q(total * savings_ideal_pct)
        assigned += result['savings']

        # ── Other categories: proportional, scaled to remaining budget ────
        other_cats = ['food', 'transportation', 'school_expenses',
                      'personal_needs', 'emergency_fund']
        remaining_for_others = max(ZERO, total - assigned)

        # Sum of ideal pcts for other_cats (excluding savings and 'other')
        other_ideal_total = sum(
            Decimal(str(targets[cat][1])) for cat in other_cats
        )

        if other_ideal_total > ZERO and remaining_for_others > ZERO:
            for i, cat in enumerate(other_cats):
                if i == len(other_cats) - 1:
                    # Last cat before 'other': absorb any rounding so far
                    already = sum(result.get(c, ZERO) for c in other_cats[:i])
                    result[cat] = max(ZERO, _q(remaining_for_others - already))
                else:
                    ideal_pct = Decimal(str(targets[cat][1])) / other_ideal_total
                    result[cat] = _q(remaining_for_others * ideal_pct)
                assigned += result[cat]
        else:
            for cat in other_cats:
                result[cat] = ZERO

        # ── 'other' absorbs rounding remainder ───────────────────────────
        result['other'] = max(ZERO, _q(total - sum(result.values(), ZERO)))

        return result

    # ── Reason strings ─────────────────────────────────────────────────────

    def _reason(self, cat: str, amount: Decimal, total: Decimal) -> str:
        pct = int(amount / total * 100) if total > ZERO else 0
        reasons = {
            'food': f"Suggested ₱{amount:.2f} ({pct}%) — food is the most essential daily need.",
            'transportation': f"Suggested ₱{amount:.2f} ({pct}%) — covers daily commute costs.",
            'school_expenses': f"Suggested ₱{amount:.2f} ({pct}%) — for materials and school fees.",
            'personal_needs': f"Suggested ₱{amount:.2f} ({pct}%) — small personal items and hygiene.",
            'savings': f"Suggested ₱{amount:.2f} ({pct}%) — building your savings habit.",
            'emergency_fund': f"Suggested ₱{amount:.2f} ({pct}%) — a buffer for unexpected costs.",
            'other': f"Suggested ₱{amount:.2f} ({pct}%) — flexible spending for miscellaneous needs.",
        }
        return reasons.get(cat, f"Suggested ₱{amount:.2f} ({pct}%).")

    # ── Main entry point ───────────────────────────────────────────────────

    def respond(self, intent: str) -> dict:
        """
        Dispatch to the correct intent handler and return a normalised response.

        Returns
        -------
        dict with keys:
          intent, message, follow_up, suggestions, breakdown (optional)
        """
        handlers = {
            'help_budget':     self._intent_help_budget,
            'daily_limit':     self._intent_daily_limit,
            'save_more':       self._intent_save_more,
            'afford_goal':     self._intent_afford_goal,
            'review_spending': self._intent_review_spending,
        }
        handler  = handlers.get(intent, self._intent_help_budget)
        result   = handler()
        result.setdefault('intent',      intent)
        result.setdefault('follow_up',   None)
        result.setdefault('suggestions', [])
        result.setdefault('breakdown',   [])
        return result

    @staticmethod
    def intent_list() -> list:
        """Return the list of available intents for the UI."""
        return [
            {'key': k, 'label': v, 'icon': i}
            for k, v, i in [
                ('help_budget',     'Help me budget my allowance',  'fa-wand-magic-sparkles'),
                ('daily_limit',     'How much can I spend today?',  'fa-calendar-day'),
                ('save_more',       'How can I save more?',         'fa-piggy-bank'),
                ('afford_goal',     'Can I afford my goal?',        'fa-bullseye'),
                ('review_spending', 'Review my spending',           'fa-chart-bar'),
            ]
        ]
