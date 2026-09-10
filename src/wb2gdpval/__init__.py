"""wb2gdpval — convert WorkBench task bundles into GDPval-format packages.

The package is the productionised successor of the single-file ``wb2gdpval.py``
(last standalone version 1.4.0). Conversion semantics are preserved exactly;
see ``docs/PROCESS.md`` for the process manual and the per-bundle blocks.
"""

__version__ = "2.0.0"

# Last single-file script version whose behaviour Mode A (one2one) reproduces.
LINEAGE_VERSION = "1.4.0"
