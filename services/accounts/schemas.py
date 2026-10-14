"""Bounded public API inputs."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AccessApplication(Input):
    name: str = Field(min_length=1, max_length=200)
    organization: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=2000)


class Decision(Input):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=2000)
    public_note: str = Field(default="", max_length=1000)


class Invite(Input):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def email_shape(cls, value: str) -> str:
        if value.count("@") != 1 or any(c.isspace() for c in value):
            raise ValueError("An email address is required")
        local, domain = value.rsplit("@", 1)
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("An email address is required")
        return value.casefold()


class Claim(Input):
    token: str = Field(min_length=40, max_length=200)


class UserPatch(Input):
    role: Literal["member", "admin"] | None = None
    status: Literal["active", "suspended"] | None = None
