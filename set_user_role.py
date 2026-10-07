import sys
import logging
from sqlalchemy import select
from app.db.session import SessionLocal, init_db
from app.db.models import User

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def set_user_role(email: str, role: str):
    role = role.lower().strip()
    if role not in ["user", "admin", "owner"]:
        logger.error("Role must be one of: 'user', 'admin', 'owner'")
        return

    init_db()
    db = SessionLocal()
    try:
        stmt = select(User).where(User.email == email.lower().strip())
        user = db.execute(stmt).scalars().first()
        if not user:
            logger.error(f"User with email '{email}' not found.")
            return

        old_role = user.role or "user"
        user.role = role
        db.commit()
        db.refresh(user)
        logger.info(f"✅ Successfully updated user '{user.email}' (ID: {user.id}) role: '{old_role}' -> '{role}'")
    except Exception as e:
        logger.error(f"Failed to update user role: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python set_user_role.py <email> <role>")
        print("Example: python set_user_role.py admin@example.com owner")
        sys.exit(1)

    user_email = sys.argv[1]
    target_role = sys.argv[2]
    set_user_role(user_email, target_role)
