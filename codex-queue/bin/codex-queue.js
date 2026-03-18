#!/usr/bin/env node
'use strict';

const queue = require('../index.js');

queue.main().catch((error) => {
  const failure = queue.normalizeError(error);
  console.error(failure.error);
  process.exit(failure.exitCode);
});
