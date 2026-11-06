#!/usr/bin/env node
// Install platform-native optional packages that `npm ci` skipped (npm/cli#4828). A lockfile
// written on macOS or x64 can leave out e.g. @rollup/rollup-linux-arm64-gnu even though it is
// listed, and `npm install --no-save` keeps the same tree. This walks node_modules for packages
// whose optionalDependencies name this platform, and installs the missing ones at the exact
// version asked for by unpacking its tarball, so the lockfile and package.json stay untouched.
"use strict";
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const root = path.resolve("node_modules");
const platform = `${process.platform}-${process.arch}`; // e.g. linux-arm64
const musl = !(process.report && process.report.getReport().header.glibcVersionRuntime);

function packageDirs(dir) {
	const out = [];
	for (const name of fs.existsSync(dir) ? fs.readdirSync(dir) : []) {
		if (name.startsWith(".")) continue;
		const full = path.join(dir, name);
		if (name.startsWith("@")) out.push(...packageDirs(full));
		else if (fs.existsSync(path.join(full, "package.json"))) out.push(full);
	}
	return out;
}

function loadable(dep) {
	try {
		require.resolve(dep, { paths: [path.dirname(root)] });
		return true;
	} catch {
		return false;
	}
}

const wanted = new Map();
for (const dir of packageDirs(root)) {
	let pkg;
	try {
		pkg = JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8"));
	} catch {
		continue;
	}
	for (const [dep, range] of Object.entries(pkg.optionalDependencies || {})) {
		if (!dep.includes(platform)) continue;
		if (/musl/.test(dep) !== musl) continue; // gnu/plain on glibc, musl on musl
		if (loadable(dep)) continue; // a dir with no loadable entry point counts as missing
		wanted.set(dep, `${dep}@${range}`);
	}
}

if (wanted.size === 0) process.exit(0);
// `npm install` re-reifies the same tree and drops them again, so unpack each tarball directly
const tmp = fs.mkdtempSync(path.join(require("os").tmpdir(), "tithe-optional-"));
for (const [dep, spec] of wanted) {
	console.log(`installing skipped native optional package: ${spec}`);
	const tgz = execFileSync("npm", ["pack", spec, "--pack-destination", tmp, "--silent"], {
		encoding: "utf8",
	}).trim().split("\n").pop();
	const dest = path.join(root, dep);
	fs.rmSync(dest, { recursive: true, force: true }); // npm can leave a partial package behind
	fs.mkdirSync(dest, { recursive: true });
	execFileSync("tar", ["-xzf", path.join(tmp, tgz), "-C", dest, "--strip-components=1"]);
}
