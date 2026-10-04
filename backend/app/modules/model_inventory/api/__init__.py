from app.modules.model_inventory.api.applications import application_router
from app.modules.model_inventory.api.deployments import deployment_router
from app.modules.model_inventory.api.routes import router

__all__ = ["application_router", "deployment_router", "router"]
