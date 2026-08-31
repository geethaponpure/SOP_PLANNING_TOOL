"""API service layer — the business logic and cached loaders the routers call.

Split out of the original monolithic ``main.py``:
  - ``common`` : tiny shared helpers (live flag, safe-call, Excel streaming)
  - ``core``   : the core dataset caches (synthetic + engine passes)
  - ``live``   : the live-CRM / file loaders and plan builders
"""
