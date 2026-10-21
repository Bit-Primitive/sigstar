"""sigstar: explain, forecast and alert on crypto price moves from every angle."""

import os

__version__ = "0.1.0.dev0"

# No telemetry. dlt and dbt both send anonymous usage statistics unless told not to, and
# these switches must be set before either library is imported.
os.environ["RUNTIME__DLTHUB_TELEMETRY"] = "false"
os.environ["DBT_SEND_ANONYMOUS_USAGE_STATS"] = "false"

# The statistical models here are tiny; multi-threaded BLAS spends more time starting
# threads than computing (measured: a 3-asset forecast went from about 40s to 4s per
# asset). Users can still override these.
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
