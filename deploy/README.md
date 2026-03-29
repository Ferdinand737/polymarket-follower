# Deployment

## Systemd Service

To run the follower as a systemd user service:

```bash
# Copy the service file
cp deploy/polymarket-follower.service ~/.config/systemd/user/

# Reload systemd
systemctl --user daemon-reload

# Enable and start the service
systemctl --user enable polymarket-follower
systemctl --user start polymarket-follower

# Check status
systemctl --user status polymarket-follower

# View logs
journalctl --user -u polymarket-follower -f
```
