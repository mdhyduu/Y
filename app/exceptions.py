# exceptions.py
class TokenError(Exception):
    """Base exception for token related errors"""
    pass

class TokenRefreshFailed(TokenError):
    """When token refresh fails"""
    pass

class InvalidTokenError(TokenError):
    """When token is invalid"""
    pass
# exceptions.py

class TokenRefreshFailed(Exception):
    """استثناء يرفع عند فشل تجديد التوكن"""
    
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code  # يمكن استخدامه لتحديد نوع الخطأ
        self.message = message

    def __str__(self):
        return f"{self.message} (Code: {self.code})" if self.code else self.message