// Trusted host bridge. All user code runs through SRT's dedicated Windows account.
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
import {spawn} from 'node:child_process';

const [entry, mode, policyFile, python, limitsFile, payload] = process.argv.slice(2);
let manager;
let child;
let stopRequested = false;
try {
    const srt = await import(pathToFileURL(entry).href);
    const native = {srtWin: srt.resolveSrtWin({path: srt.VENDORED_SRT_WIN_EXE})};
    if (mode === 'status') {
        const result = await srt.checkWindowsDependenciesAsync(native);
        console.log(JSON.stringify({ready: result.errors.length === 0, errors: result.errors}));
    } else if (mode === 'install') {
        await srt.installWindowsSandboxAsync(native);
        console.log('Windows sandbox account and network isolation initialized.');
    } else if (mode === 'run') {
        manager = srt.SandboxManager;
        const config = srt.SandboxRuntimeConfigSchema.parse(JSON.parse(fs.readFileSync(policyFile, 'utf8')));
        config.windows = {...config.windows, srtWin: {path: srt.VENDORED_SRT_WIN_EXE}};
        const stop = () => { stopRequested = true; child?.kill(); };
        process.on('SIGINT', stop);
        process.on('SIGTERM', stop);
        if (process.platform === 'win32') process.on('SIGBREAK', stop);
        await manager.initialize(config);
        if (stopRequested) throw new Error('Sandbox invocation cancelled during initialization');
        const source = fs.readFileSync(limitsFile, 'utf8');
        // Pass opaque base64 data directly to Python. Never reparse agent text on a host shell.
        const wrapped = await manager.wrapWithSandboxArgv(payload,
            {exe: python, args: ['-I', '-c', source]});
        if (stopRequested) throw new Error('Sandbox invocation cancelled before execution');
        const exitCode = await new Promise((resolve, reject) => {
            child = spawn(wrapped.argv[0], wrapped.argv.slice(1),
                {shell: false, stdio: 'inherit', env: wrapped.env});
            child.once('error', reject);
            child.once('exit', (code) => resolve(code ?? 125));
        });
        process.exitCode = exitCode;
    } else {
        throw new Error('Unsupported Windows sandbox operation');
    }
} catch (error) {
    console.error(`ClawCross Windows sandbox initialization failed: ${error.message}`);
    process.exitCode = 125;
} finally {
    if (manager) {
        try { await manager.reset(); }
        catch (error) {
            console.error(`ClawCross Windows sandbox cleanup failed: ${error.message}`);
            process.exitCode = 125;
        }
    }
}
