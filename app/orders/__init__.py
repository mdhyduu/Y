# orders/__init__.py
from flask import Blueprint

# All routes will be registered under this blueprint
# Adjust template_folder and static_folder if your structure differs
orders_bp = Blueprint('orders', __name__,
                    template_folder='../templates',
                    static_folder='../static')

# Import the route modules to register them with the blueprint
from . import routes
from . import sync
from . import assignment
from . import status_management
from . import custom_orders
from . import utils_routes
from . import print_utils
