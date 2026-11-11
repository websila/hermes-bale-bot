"""Bale env -> gateway config bridge (PlatformEntry.env_overrides)."""

from gateway.config import Platform
from gateway.platform_env import EnvCred, home_step, reply_mode_step

# User plugins are not in the bundled platform scan, so create the enum member
# before EnvCred/home_step capture it (Platform("bale") would otherwise fail).
_BALE = Platform._value2member_map_.get("bale") or Platform._add_pseudo_member("bale")

ENV_ENABLE_CREDENTIALS = ("BALE_BOT_TOKEN",)
ENV_OVERRIDES = (
    EnvCred(_BALE, ("BALE_BOT_TOKEN",), token="BALE_BOT_TOKEN"),
    reply_mode_step(_BALE, "BALE_REPLY_TO_MODE"),
    home_step(_BALE, "BALE_HOME_CHANNEL"),
)

AUTHZ = {
    "allowed_users_env": "BALE_ALLOWED_USERS",
    "allow_all_env": "BALE_ALLOW_ALL_USERS",
    "allow_bots_env": "BALE_ALLOW_BOTS",
    "group_allowed_chats_env": "BALE_GROUP_ALLOWED_CHATS",
    "allowlist_suppresses_pairing": True,
}
