"""Live data integration layer.

Replaces the synthetic ``data.build_dataset()`` with real feeds from:
  - CRM (SQL Server, CRMPROD @ 10.1.0.146) -- projections, SOC, quotes,
    dispatch history, PTO/PTS + item master.
  - Oracle staging -- lot-wise stock, BOM, PO receipts.

Switched on by the ``DATA_SOURCE=live`` environment variable. Everything in the
planning engine consumes the same ``data`` dict shape, so only this layer changes.
"""
