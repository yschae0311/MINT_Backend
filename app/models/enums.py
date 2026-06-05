import enum


class UserRole(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    member = "member"
    viewer = "viewer"


class SourceType(str, enum.Enum):
    rss = "rss"
    webpage = "webpage"
    news_page = "news_page"
    notice_page = "notice_page"
    manual = "manual"


class TrustLevel(str, enum.Enum):
    high = "high"
    medium = "medium"
    low = "low"


class DiscoveryType(str, enum.Enum):
    manual = "manual"
    ai_discovered = "ai_discovered"
    user_submitted = "user_submitted"


class BoardType(str, enum.Enum):
    trusted = "trusted"
    discovery = "discovery"


class PostStatus(str, enum.Enum):
    pending = "pending"
    published = "published"
    hidden = "hidden"
    deleted = "deleted"
    promoted = "promoted"


class Importance(str, enum.Enum):
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class CreatedBy(str, enum.Enum):
    admin = "admin"
    crawler = "crawler"
    ai_discovery = "ai_discovery"


class SlackPurpose(str, enum.Enum):
    daily = "daily"
    urgent = "urgent"
    review = "review"
    all = "all"


class ChannelType(str, enum.Enum):
    slack = "slack"
    email = "email"
    telegram = "telegram"


class NotificationStatus(str, enum.Enum):
    success = "success"
    failed = "failed"
    pending = "pending"
