from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class UserRegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100, examples=["John Doe"])
    email: EmailStr = Field(..., examples=["user@example.com"])
    password: str = Field(..., min_length=8, examples=["StrongP@ss123"])
    confirm_password: str = Field(..., examples=["StrongP@ss123"])

    @model_validator(mode="after")
    def check_passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$", examples=["123456"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(..., min_length=8)
    confirm_password: str = Field(...)

    @model_validator(mode="after")
    def check_passwords_match(self):
        if self.new_password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class ResendOTPRequest(BaseModel):
    email: EmailStr
    otp_type: Literal["registration", "forgot_password"] = "registration"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class CheckPasswordStrengthRequest(BaseModel):
    password: str


class UserOut(BaseModel):
    id: int
    full_name: str
    email: str
    is_verified: bool
    is_active: bool
    role: str = "user"
    created_at: datetime

    class Config:
        from_attributes = True


class UpdateUserRoleRequest(BaseModel):
    target_user_id: int
    new_role: Literal["user", "admin", "owner"]


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class MessageResponse(BaseModel):
    message: str
    otp_active: Optional[bool] = None


class PasswordStrengthResponse(BaseModel):
    is_valid: bool
    score: int
    feedback: List[str]
