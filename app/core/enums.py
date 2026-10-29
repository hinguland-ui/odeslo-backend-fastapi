"""All status values and fixed choices used across the platform (stored as strings in the DB)."""

from enum import StrEnum


class UserType(StrEnum):
    SUPER_ADMIN = "super_admin"
    SOCIETY_ADMIN = "society_admin"
    RESIDENT = "resident"
    STAFF = "staff"
    PARTNER = "partner"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


# ---------- Society ----------
class SocietyStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class SocietyType(StrEnum):
    APARTMENT = "apartment"
    ROW_HOUSE = "row_house"
    DUPLEX = "duplex"
    MIXED = "mixed"


class UnitType(StrEnum):
    FLAT = "flat"
    DUPLEX = "duplex"
    SHOP = "shop"
    ROW_HOUSE = "row_house"


class OccupancyStatus(StrEnum):
    VACANT = "vacant"
    OWNER = "owner"
    TENANT = "tenant"


class Feature(StrEnum):
    GROUP_DEALS = "group_deals"
    MASJID = "masjid"
    COMPLAINTS = "complaints"
    REQUESTS = "requests"
    MAINTENANCE = "maintenance"
    STAFF = "staff"
    ANNOUNCEMENTS = "announcements"


DEFAULT_FEATURES = {f.value: True for f in Feature}


# ---------- Society admin ----------
class AdminRole(StrEnum):
    PRIMARY = "primary"
    CO_ADMIN = "co_admin"


class Designation(StrEnum):
    SECRETARY = "secretary"
    CHAIRMAN = "chairman"
    TREASURER = "treasurer"
    MANAGER = "manager"
    OTHER = "other"


class AdminPermission(StrEnum):
    RESIDENTS = "residents"
    UNITS = "units"
    MAINTENANCE = "maintenance"
    PAYMENTS = "payments"
    REFUNDS = "refunds"
    COMPLAINTS = "complaints"
    REQUESTS = "requests"
    STAFF = "staff"
    SERVICE_JOBS = "service_jobs"
    DEALS = "deals"
    ANNOUNCEMENTS = "announcements"
    MASJIDS = "masjids"
    NOTIFICATIONS = "notifications"
    REPORTS = "reports"


CO_ADMIN_PRESETS: dict[str, list[str]] = {
    "treasurer": [AdminPermission.MAINTENANCE, AdminPermission.PAYMENTS, AdminPermission.REFUNDS, AdminPermission.REPORTS],
    "operations": [
        AdminPermission.COMPLAINTS,
        AdminPermission.REQUESTS,
        AdminPermission.STAFF,
        AdminPermission.SERVICE_JOBS,
        AdminPermission.DEALS,
    ],
}


# ---------- Resident ----------
class ResidentType(StrEnum):
    OWNER = "owner"
    TENANT = "tenant"
    FAMILY = "family"


class ResidentStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    REJECTED = "rejected"
    DEACTIVATED = "deactivated"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class DocumentType(StrEnum):
    INDEX_II = "index_ii"
    SALE_DEED = "sale_deed"
    RENT_AGREEMENT = "rent_agreement"
    ELECTRICITY_BILL = "electricity_bill"
    ID_PROOF = "id_proof"
    OTHER = "other"


# ---------- Staff / partners ----------
class StaffType(StrEnum):
    GUARD = "guard"
    HOUSEKEEPING = "housekeeping"
    PLUMBER = "plumber"
    ELECTRICIAN = "electrician"
    GARDENER = "gardener"
    OTHER = "other"


class ServiceCategory(StrEnum):
    AC = "ac"
    PLUMBING = "plumbing"
    ELECTRICAL = "electrical"
    PEST_CONTROL = "pest_control"
    CLEANING = "cleaning"
    CARPENTRY = "carpentry"
    PAINTING = "painting"
    APPLIANCE_REPAIR = "appliance_repair"
    WATER_TANK_CLEANING = "water_tank_cleaning"
    OTHER = "other"


# ---------- Group deals & bookings ----------
class DealStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    FULL = "full"
    CLOSED = "closed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BookingStatus(StrEnum):
    PENDING_PAYMENT = "pending_payment"
    CONFIRMED = "confirmed"
    WAITLISTED = "waitlisted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# Statuses that occupy capacity on a deal. Cancelled is never counted.
CAPACITY_BOOKING_STATUSES = (BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED, BookingStatus.COMPLETED)
ACTIVE_BOOKING_STATUSES = (BookingStatus.PENDING_PAYMENT, BookingStatus.CONFIRMED, BookingStatus.WAITLISTED)


class BookingPaymentStatus(StrEnum):
    UNPAID = "unpaid"
    PAID = "paid"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"


# ---------- Service jobs ----------
class JobStatus(StrEnum):
    CREATED = "created"
    ASSIGNED = "assigned"
    ACCEPTED = "accepted"
    ON_THE_WAY = "on_the_way"
    STARTED = "started"
    COMPLETED = "completed"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"


class AssigneeType(StrEnum):
    STAFF = "staff"
    PARTNER = "partner"


class PayoutStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    PAID = "paid"


# ---------- Money ----------
class PaymentStatus(StrEnum):
    INITIATED = "initiated"
    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"


class PaymentRefType(StrEnum):
    BOOKING = "booking"
    MAINTENANCE = "maintenance"


class PaymentMode(StrEnum):
    ONLINE = "online"
    CASH = "cash"
    CHEQUE = "cheque"
    UPI = "upi"
    BANK_TRANSFER = "bank_transfer"


class RefundStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    PROCESSED = "processed"
    COMPLETED = "completed"
    REJECTED = "rejected"


class RefundMode(StrEnum):
    GATEWAY = "gateway"
    MANUAL = "manual"


class BillStatus(StrEnum):
    GENERATED = "generated"
    DUE = "due"
    PAID = "paid"
    OVERDUE = "overdue"
    PARTIALLY_PAID = "partially_paid"


UNPAID_BILL_STATUSES = (BillStatus.GENERATED, BillStatus.DUE, BillStatus.OVERDUE, BillStatus.PARTIALLY_PAID)


class BillingCycle(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


# ---------- Complaints & requests ----------
class ComplaintStatus(StrEnum):
    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REOPENED = "reopened"


OPEN_COMPLAINT_STATUSES = (
    ComplaintStatus.OPEN,
    ComplaintStatus.ASSIGNED,
    ComplaintStatus.IN_PROGRESS,
    ComplaintStatus.REOPENED,
)


class ComplaintCategory(StrEnum):
    PLUMBING = "plumbing"
    ELECTRICAL = "electrical"
    LIFT = "lift"
    SECURITY = "security"
    CLEANLINESS = "cleanliness"
    PARKING = "parking"
    WATER = "water"
    NOISE = "noise"
    OTHER = "other"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class RequestType(StrEnum):
    NOC = "noc"
    MOVE_IN = "move_in"
    MOVE_OUT = "move_out"
    PARKING = "parking"
    DOCUMENT = "document"
    RENOVATION = "renovation"
    OTHER = "other"


class RequestStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    APPROVED = "approved"
    REJECTED = "rejected"
    CLOSED = "closed"


# ---------- Masjid ----------
class MasjidStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    REJECTED = "rejected"


# ---------- Announcements & notifications ----------
class AnnouncementCategory(StrEnum):
    NOTICE = "notice"
    EVENT = "event"
    MEETING = "meeting"
    EMERGENCY = "emergency"
    GENERAL = "general"


class NotificationCategory(StrEnum):
    ACCOUNT = "account"
    REGISTRATION = "registration"
    DEAL = "deal"
    BOOKING = "booking"
    SERVICE_JOB = "service_job"
    PAYMENT = "payment"
    REFUND = "refund"
    MAINTENANCE = "maintenance"
    COMPLAINT = "complaint"
    REQUEST = "request"
    ANNOUNCEMENT = "announcement"
    NAMAZ = "namaz"
    MASJID = "masjid"
    GENERAL = "general"
