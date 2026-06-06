"""Pipeline stage adapters.

Import ``simple_ar.pipeline_stages.registry`` when the full default handler
registry is needed. Keeping package import side-effect free prevents domain
services from pulling every stage into memory and avoids circular imports.
"""
