import enum


class StrategyType(enum.StrEnum):
    PUT_CREDIT_SPREAD = "put_credit_spread"
    # True Wheel put: cash secured, assignment accepted.
    CASH_SECURED_PUT = "cash_secured_put"
    # Short Put Income: sold for its premium, bought back before expiration.
    SHORT_PUT = "short_put"
    COVERED_CALL = "covered_call"


class OpportunityStatus(enum.StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class DecisionAction(enum.StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class RejectReason(enum.StrEnum):
    PREMIUM_TOO_LOW = "premium_too_low"
    SECTOR_CONCENTRATION = "sector_concentration"
    NO_CONVICTION = "no_conviction"
    NEWS = "news"
    OTHER = "other"


class InstrumentType(enum.StrEnum):
    OPTION = "option"
    STOCK = "stock"


class OptionType(enum.StrEnum):
    PUT = "put"
    CALL = "call"


class Side(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"


class PositionStatus(enum.StrEnum):
    PENDING = "pending"
    OPEN = "open"
    CLOSED = "closed"
    EXPIRED = "expired"
    ASSIGNED = "assigned"
    # The opening order never filled (expired, canceled or rejected).
    CANCELED = "canceled"


class ExitReason(enum.StrEnum):
    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    TIME_EXIT = "time_exit"
    MANUAL = "manual"
    EXPIRATION = "expiration"
    ASSIGNMENT = "assignment"
    CALLED_AWAY = "called_away"


class OrderPurpose(enum.StrEnum):
    OPEN = "open"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIME_EXIT = "time_exit"
    MANUAL_CLOSE = "manual_close"


class OrderStatus(enum.StrEnum):
    NEW = "new"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PositionEventType(enum.StrEnum):
    OPENED = "opened"
    TAKE_PROFIT_PLACED = "take_profit_placed"
    STOP_TRIGGERED = "stop_triggered"
    TIME_EXIT_TRIGGERED = "time_exit_triggered"
    CLOSED = "closed"
    EXPIRED = "expired"
    ASSIGNED = "assigned"
    CALLED_AWAY = "called_away"
    RECONCILIATION_MISMATCH = "reconciliation_mismatch"
    ORDER_REJECTED = "order_rejected"
