"""User mapping management routes."""

from typing import Optional
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from app.services.user_mapping_service import user_mapping_service
from app.database.repositories import UserMappingRepository

router = APIRouter(tags=["User Mappings"])
mapping_repo = UserMappingRepository()


class UserMappingCreate(BaseModel):
    jira_user_id: str
    mattermost_user_id: str
    display_name: str
    active: bool = True


@router.get("/user-mappings")
async def list_user_mappings():
    """List all registered Jira <-> Mattermost user mappings."""
    return {"mappings": mapping_repo.list_mappings()}


@router.post("/user-mappings")
async def create_or_update_mapping(mapping: UserMappingCreate):
    """Register or update a Jira <-> Mattermost user mapping."""
    mid = user_mapping_service.register_mapping(
        jira_user_id=mapping.jira_user_id,
        mattermost_user_id=mapping.mattermost_user_id,
        display_name=mapping.display_name
    )
    return {
        "status": "success",
        "mapping_id": mid,
        "jira_user_id": mapping.jira_user_id,
        "mattermost_user_id": mapping.mattermost_user_id,
        "display_name": mapping.display_name
    }
