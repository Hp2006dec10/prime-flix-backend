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

3. Generate a secure email verification OTP.

4. Send OTP via email.

5. User submits OTP.

6. Backend verifies OTP.

7. Account becomes active.

---

## 2. Login

Allow login using:

* Email
* Password

Requirements:

* Email must already be verified.
* Passwords must be hashed using bcrypt.
* Return JWT access token and refresh token.
* Support logout from current session.

---

## 3. Forgot Password

Flow:

1. User enters email, verify whether the email exists.
2. Send password reset OTP.
3. User verifies OTP.
4. User sets new password.
5. Invalidate current session.

---

# OTP System Requirements

Implement OTP handling with production-grade security.

Requirements:

### OTP Generation

* Use cryptographically secure random generation.
* OTP length: 6 digits.
* Store only hashed OTP values.
* Never store plaintext OTPs.

### OTP Expiration

* OTP expiry: 10 minutes.

### OTP Reuse Prevention

* OTP becomes invalid immediately after successful verification.

### OTP Resend Strategy

If user requests resend while OTP is still active:

DO NOT generate a new OTP.

Instead:

* Inform user that a valid OTP was already sent.

Example:

"A valid OTP already sent. Please use that OTP instead."

---

# Brute Force Protection

Implement protection against:

## OTP brute forcing

Example limits:

* Maximum 5 verification attempts.
* Lock verification for 30 minutes after limit exceeded.

---

## Login brute forcing

Implement:

* Temporary account lock after repeated failures.

Example:

3 failed attempts:

* Lock account for 30 minutes.

---

## OTP Request Abuse Prevention

Prevent users from spamming OTP generation. As already said, if an OTP is already active, notify the user and do not send OTP again.

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

* users (have columns for active/inactive & status column email verification completed or not, creation time).
* otps (at a time, one user can have only one active otp and specify type of the OTP whether it is for registration or forgot password).

---

# Security Requirements

Include:

* HTTPS only
* bcrypt password hashing
* CSRF considerations
* JWT signing
* Secure email templates
* OTP hashing
* Audit logging
* Account lockouts

---

# User Experience Requirements

Provide UX for:

* Suggest using previously sent OTP if still valid.
* Clear validation messages.
* Friendly security messages.
* Automatic login after registration verification.

---