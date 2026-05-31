from enum import Enum


class CategoryScope(str, Enum):
    event = "event"
    company = "company"
    personal = "personal"


class EventStatus(str, Enum):
    booked    = "booked"
    active    = "active"
    completed = "completed"
    cancelled = "cancelled"


class EventType(str, Enum):
    wedding    = "Wedding"
    engagement = "Engagement"
    reception  = "Reception"
    portrait   = "Portrait"
    maternity  = "Maternity"
    corporate  = "Corporate"
    other      = "Other"


class LeadSource(str, Enum):
    referral  = "Referral"
    instagram = "Instagram"
    friends   = "Friends"
    walkin    = "Walk-in"
    website   = "Website"
    other     = "Other"


class PaymentStatus(str, Enum):
    paid = "paid"
    pending = "pending"
    partial = "partial"


class LeadStatus(str, Enum):
    new    = "new"
    quoted = "quoted"
    won    = "won"
    lost   = "lost"
    cold   = "cold"


class DeliveryStatus(str, Enum):
    shooting_done = "shooting_done"
    editing       = "editing"
    review        = "review"
    delivered     = "delivered"


class FollowupStatus(str, Enum):
    pending   = "pending"
    scheduled = "scheduled"
    done      = "done"


class PaymentType(str, Enum):
    cash       = "Cash"
    credit     = "Credit"
    upi        = "UPI"
    current_ac = "Current A/C"
    savings_ac = "Savings A/C"
