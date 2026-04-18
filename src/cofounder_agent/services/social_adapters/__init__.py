"""Social media distribution adapters.

Each adapter handles posting to one platform. All follow the same interface:

    async def post(text: str, url: str, **kwargs) -> dict

Returns {"success": bool, "post_id": str | None, "error": str | None}

Adapters read their credentials from app_settings via site_config.get_secret().
"""
