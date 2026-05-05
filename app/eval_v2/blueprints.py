from flask import Blueprint

eval_v2_bp  = Blueprint('eval_v2',     __name__, url_prefix='/eval-v2')
eval_v2_api = Blueprint('eval_v2_api', __name__, url_prefix='/api/v2')
