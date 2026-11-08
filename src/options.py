"""The options the admin page can change. Each can still start from its environment variable; a saved value wins."""

from engine import DEFAULT_VERSION
from settings import Setting

SETTINGS = [
    Setting("update_check_hours", "Check for updates every (hours)", "float", 6.0, "SERVICE_UPDATE_CHECK_HOURS", 0, 720,
            help="0 = only when you press Check now"),
    Setting("max_inflight", "Calls allowed to wait for the model", "int", 0, "SERVICE_MAX_INFLIGHT", 0, 10000,
            help="Per replica; more get 429. 0 = 4"),
    Setting("cache_size", "Answers kept per replica", "int", 64, "SERVICE_CACHE_SIZE", 0, 100000,
            help="The last answers for the same text, voice and speed, sent again without speaking. Each is at most 1 MB. 0 = off"),
    Setting("version", "Paradee version", "text", DEFAULT_VERSION, "SERVICE_VERSION", restart=True, blank=False,
            help="1.2 speaks four voices; 1.1 and 1.0 speak af_heart only. After a restart, download it from the Updates card"),
    Setting("warm", "Load the model at start", "bool", True, "SERVICE_WARM", restart=True),
]
