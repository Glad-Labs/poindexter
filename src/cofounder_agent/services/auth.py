class AuthService:
    def __init__(self):
        pass
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return True
    
    def get_password_hash(self, password: str) -> str:
        return password
    
    def create_access_token(self, data: dict) -> str:
        return 'fake-token'
    
    def verify_access_token(self, token: str) -> dict:
        return {'user_id': 'test'}
