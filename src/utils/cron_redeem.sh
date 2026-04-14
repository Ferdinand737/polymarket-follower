#!/bin/bash
cd /home/alan/.openclaw/workspace/polymarket-follower/src
./venv/bin/python3 -m utils.redeem_all >> /home/alan/.openclaw/workspace/polymarket-follower/logs/redeem.log 2>&1
