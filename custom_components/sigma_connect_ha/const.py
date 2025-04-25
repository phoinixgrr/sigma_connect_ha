# File: custom_components/sigma_connect_ha/const.py

DOMAIN = "sigma_connect_ha"

# Config keys for advanced settings
CONF_UPDATE_INTERVAL = "update_interval"
CONF_RETRY_TOTAL = "retry_total"
CONF_RETRY_BACKOFF_FACTOR = "retry_backoff_factor"
CONF_RETRY_ATTEMPTS_FOR_HTML = "retry_attempts_for_html"
CONF_MAX_TOTAL_ATTEMPTS = "max_total_attempts"
CONF_MAX_ACTION_ATTEMPTS = "max_action_attempts"
CONF_ACTION_BASE_DELAY = "action_base_delay"
CONF_POST_ACTION_EXTRA_DELAY = "post_action_extra_delay"
CONF_MAX_CONSECUTIVE_FAILURES = "max_consecutive_failures"

# Default values for advanced settings
DEFAULT_UPDATE_INTERVAL = 10            # seconds between polling updates
DEFAULT_RETRY_TOTAL = 5                 # HTTP retry attempts on network errors
DEFAULT_RETRY_BACKOFF_FACTOR = 0.5      # Exponential backoff multiplier for HTTP retries
DEFAULT_RETRY_ATTEMPTS_FOR_HTML = 3     # Retries when parsing HTML fails
DEFAULT_MAX_TOTAL_ATTEMPTS = 3          # Coordinator fetch retry attempts
DEFAULT_MAX_ACTION_ATTEMPTS = 5         # Attempts for arm/disarm/stay actions
DEFAULT_ACTION_BASE_DELAY = 2.0         # Seconds base delay between action retries
DEFAULT_POST_ACTION_EXTRA_DELAY = 5.0   # Seconds delay after action before verifying
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3    # Number of consecutive polling failures before marking data unavailable
