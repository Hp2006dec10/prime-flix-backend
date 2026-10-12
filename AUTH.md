# Authentication Features

Implement the following authentication flows:

## 1. User Registration

Flow:

1. User enters:
   * Full name
   * Email
   * Password
   * Confirm password

2. Backend validates:
   * Email format
   * Password policy
   * Duplicate account prevention

3. Account is created and immediately active.
4. Returns JWT access token and refresh token for instant login.

---

## 2. Login

Allow login using:

* Email
* Password

Requirements:

* Passwords must be hashed using bcrypt.
* Return JWT access token and refresh token.
* Support logout from current session.

---


# Brute Force Protection

Implement protection against:

## Login brute forcing

Implement:

* Temporary account lock after repeated failures.
* Example: 3 failed attempts locks account for 30 minutes.

---

# Password Requirements

Define and display password policy:

Minimum:
* 8 characters

Require:
* uppercase
* lowercase
* number
* special character

Include:
* password strength meter recommendations

---

# Database Design

Schemas for:
* users (columns for id, full_name, email, hashed_password, is_active, role, failed_login_attempts, login_locked_until, created_at, updated_at).

---

# Security Requirements

Include:
* HTTPS only
* bcrypt password hashing
* JWT signing
* Audit logging
* Account lockouts

---