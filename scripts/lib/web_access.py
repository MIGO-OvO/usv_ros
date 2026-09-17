"""Write access for the vessel console; no secrets are persisted or logged."""
import hmac
import ipaddress
import os
from urllib.parse import urlsplit


def install_control_access(app):
    from flask import jsonify, request

    token = os.environ.get('USV_WEB_CONTROL_TOKEN', '')
    if token and len(token) < 16:
        raise ValueError('USV_WEB_CONTROL_TOKEN must contain at least 16 characters')

    @app.before_request
    def protect_control():
        authentication = request.path == '/api/control/auth'
        if not authentication and (not request.path.startswith('/api/') or request.method in ('GET', 'HEAD', 'OPTIONS')):
            return None
        origin = request.headers.get('Origin')
        if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
            return jsonify(success=False, message='Cross-origin control is disabled'), 403
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(success=False, message='Cross-site control is disabled'), 403

        authorization = request.headers.get('Authorization', '')
        supplied = authorization[7:] if authorization.startswith('Bearer ') else ''
        basic = request.authorization
        if basic and basic.type == 'basic' and basic.username == 'operator':
            supplied = basic.password or ''
        if token:
            if not hmac.compare_digest(supplied.encode('utf-8'), token.encode('utf-8')):
                response = jsonify(success=False, message='Control authentication required')
                response.status_code = 401
                response.headers['WWW-Authenticate'] = 'Basic realm="USV control", charset="UTF-8"'
                return response
        else:
            try:
                peer_local = ipaddress.ip_address(request.remote_addr or '').is_loopback
                host = urlsplit(request.host_url).hostname
                host_local = host == 'localhost' or ipaddress.ip_address(host or '').is_loopback
            except ValueError:
                peer_local = host_local = False
            if not (peer_local and host_local):
                return jsonify(success=False, message='Remote control is disabled; configure USV_WEB_CONTROL_TOKEN'), 403
        return None

    @app.route('/api/control/auth', methods=['GET'])
    def control_auth():
        return jsonify(success=True, message='Control access authenticated')
