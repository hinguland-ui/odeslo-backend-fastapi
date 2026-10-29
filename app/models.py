"""Imports every model so SQLAlchemy metadata knows all tables (used by create_all and scripts)."""

from app.modules.activity_logs.models import ActivityLog  # noqa: F401
from app.modules.announcements.models import Announcement  # noqa: F401
from app.modules.auth.models import OtpLog  # noqa: F401
from app.modules.bookings.models import Booking  # noqa: F401
from app.modules.complaints.models import Complaint, ComplaintComment  # noqa: F401
from app.modules.group_deals.models import DealApplication, GroupDeal  # noqa: F401
from app.modules.location_requests.models import LocationRequest  # noqa: F401
from app.modules.maintenance.models import MaintenanceBill  # noqa: F401
from app.modules.masjids.models import Masjid, MasjidTiming  # noqa: F401
from app.modules.notifications.models import DeviceToken, Notification  # noqa: F401
from app.modules.payments.models import Payment  # noqa: F401
from app.modules.refunds.models import Refund  # noqa: F401
from app.modules.requests.models import ResidentRequest  # noqa: F401
from app.modules.residents.models import FamilyMember, Resident, ResidentDocument, Vehicle  # noqa: F401
from app.modules.service_jobs.models import JobLocation, JobMessage, ServiceJob, ServiceJobUpdate  # noqa: F401
from app.modules.service_partners.models import PartnerSociety, ServicePartner  # noqa: F401
from app.modules.settings.models import PlatformSetting  # noqa: F401
from app.modules.societies.models import Society, Unit, Wing  # noqa: F401
from app.modules.society_admins.models import SocietyAdmin  # noqa: F401
from app.modules.worship_places.models import WorshipPlace  # noqa: F401
from app.modules.staff.models import Staff  # noqa: F401
from app.modules.super_admins.models import SuperAdmin  # noqa: F401
from app.modules.files.models import StoredFile  # noqa: F401
from app.modules.onboarding.models import OnboardingDraft  # noqa: F401
