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
    reddit = "reddit"
    community_forum = "community_forum"


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
    user_submitted = "user_submitted"


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


class JobType(str, enum.Enum):
    crawl_source = "crawl_source"
    crawl_source_discovery = "crawl_source_discovery"
    crawl_all_discovery = "crawl_all_discovery"
    crawl_all = "crawl_all"
    discovery_pipeline = "discovery_pipeline"
    community_discovery_pipeline = "community_discovery_pipeline"
    generate_report = "generate_report"
    send_slack_report = "send_slack_report"
    summarize_post = "summarize_post"
    purge_stale_discovery = "purge_stale_discovery"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    cancelled = "cancelled"
