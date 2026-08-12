# Nginx Setup for Matching Bot v3

## Place SSL certificates

Before starting the containers, place your SSL certificates here:

```
nginx/certs/fullchain.pem
nginx/certs/privkey.pem
```

### Generate self-signed certificates (for testing)

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/certs/privkey.pem \
  -out nginx/certs/fullchain.pem \
  -subj "/C=IR/ST=Tehran/L=Tehran/O=MatchingBot/CN=yourdomain.com"
```

### Get Let's Encrypt certificates (production)

```bash
# Install certbot
sudo apt install certbot

# Stop nginx if running
docker-compose down nginx

# Get certificate
sudo certbot certonly --standalone -d yourdomain.com

# Copy certificates
sudo cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem nginx/certs/
sudo cp /etc/letsencrypt/live/yourdomain.com/privkey.pem nginx/certs/
sudo chown $USER:$USER nginx/certs/*.pem
```

## Logs

Access and error logs are written to `nginx/logs/`.

## Reload config without downtime

```bash
docker exec match_nginx nginx -s reload
```
