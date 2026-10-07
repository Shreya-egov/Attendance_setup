"""
One import for the command-line scripts: ``import utils.hcm as H`` gives the
config, the client, the CLI helpers and the attendance planners, as the old
hcm_common module did (now split across utils/). Tests import the specific modules instead.
"""
from utils.config import *  # noqa: F401,F403
from utils.client import *  # noqa: F401,F403
from utils.cli import *  # noqa: F401,F403
from utils.attendance import *  # noqa: F401,F403
