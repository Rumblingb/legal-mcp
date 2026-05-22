#!/usr/bin/env node
const { spawn } = require('child_process');
const path = require('path');
const child = spawn('python3', [path.join(__dirname, 'server.py'), ...process.argv.slice(2)], { stdio: 'inherit', env: { ...process.env } });
child.on('error', (err) => { console.error('Failed to start legal-mcp:', err.message); process.exit(1); });
child.on('exit', (code) => process.exit(code || 0));
