from flask import Flask, request, Response
import requests
import base64
import os

app = Flask(__name__)

@app.route('/')
def proxy():
    stream_url = request.args.get('url')
    data = request.args.get('data')
    if not stream_url or not data:
        return 'Missing url or data', 400

    try:
        headers_raw = base64.b64decode(data).decode('utf-8')
        headers = dict(pair.split('=', 1) for pair in headers_raw.split('|') if '=' in pair)
        
        # Add X-Forwarded-For to mimic client IP (helps with upstream blocking)
        headers['X-Forwarded-For'] = request.remote_addr or '105.160.33.214'  # Fallback to your Kenya IP
        
        # Set timeout to avoid Vercel 30s limit
        r = requests.get(stream_url, headers=headers, stream=True, timeout=10)
        
        # Log for debugging
        print(f"Fetching {stream_url}, status: {r.status_code}")
        
        return Response(
            r.iter_content(chunk_size=1024),
            content_type=r.headers.get('Content-Type', 'application/octet-stream')
        )
    except Exception as e:
        print(f"Error: {str(e)}")
        return f'Error: {str(e)}', 502  # Use 502 for upstream errors

if __name__ == '__main__':
    if os.environ.get('VERCEL'):
        pass  # Vercel handles running
    else:
        app.run(host='0.0.0.0', port=4123)  # Local dev
