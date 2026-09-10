"""Test package.

The retry path logs a warning per attempt by design. That is right in production and pure noise in
a test run, where several tests deliberately exhaust the retries, so quieten the pipeline's loggers
for the duration. Failures still surface — unittest reports those itself.
"""

import logging

logging.getLogger("p03.elt").setLevel(logging.CRITICAL)
logging.getLogger("p03.elt.nyc311").setLevel(logging.CRITICAL)
