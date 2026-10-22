"""Spike: CPU and memory of a command's whole process tree.

Samples the process and its descendants (QtWebEngineProcess renderers, GPU
process...) from /proc via psutil. CPU is % of one core; memory is PSS, which
splits shared pages between processes instead of counting them in each.

    .venv/bin/python spikes/html_overlay/measure.py --warmup 6 --duration 15 -- <command...>
"""
import argparse
import statistics
import subprocess
import sys
import time
from collections import defaultdict

import psutil


def kind(p):
    try:
        cmd = p.cmdline()
    except psutil.Error:
        return 'gone'
    for a in cmd:
        if a.startswith('--type='):
            return a[7:]
    return 'main'


def tree(root):
    try:
        return [root] + root.children(recursive=True)
    except psutil.Error:
        return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--warmup', type=float, default=6)
    parser.add_argument('--duration', type=float, default=15)
    parser.add_argument('--interval', type=float, default=0.5)
    parser.add_argument('--label', default='')
    parser.add_argument('cmd', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    cmd = args.cmd[1:] if args.cmd[:1] == ['--'] else args.cmd

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    root = psutil.Process(proc.pid)
    time.sleep(args.warmup)

    def cpu_times():
        out = {}
        for p in tree(root):
            try:
                t = p.cpu_times()
                out[p.pid] = (kind(p), t.user + t.system)
            except psutil.Error:
                pass
        return out

    cpu_by_kind = defaultdict(list)
    pss_by_kind = defaultdict(list)
    totals_cpu, totals_pss, counts = [], [], []
    prev, prev_t = cpu_times(), time.perf_counter()
    end = prev_t + args.duration
    while time.perf_counter() < end and proc.poll() is None:
        time.sleep(args.interval)
        cur, cur_t = cpu_times(), time.perf_counter()
        dt = cur_t - prev_t
        per = defaultdict(float)
        for pid, (k, t) in cur.items():
            if pid in prev:
                per[k] += (t - prev[pid][1]) / dt * 100
        mem = defaultdict(float)
        for p in tree(root):
            try:
                mem[kind(p)] += p.memory_full_info().pss / 2**20
            except psutil.Error:
                pass
        for k, v in per.items():
            cpu_by_kind[k].append(v)
        for k, v in mem.items():
            pss_by_kind[k].append(v)
        totals_cpu.append(sum(per.values()))
        totals_pss.append(sum(mem.values()))
        counts.append(len(cur))
        prev, prev_t = cur, cur_t

    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()

    print(f'== {args.label or " ".join(cmd)}')
    print(f'   total: CPU mean {statistics.mean(totals_cpu):.0f}% (p95 {sorted(totals_cpu)[int(len(totals_cpu) * .95)]:.0f}%)  '
          f'PSS mean {statistics.mean(totals_pss):.0f} MB (max {max(totals_pss):.0f})  processes {max(counts)}')
    for k in sorted(pss_by_kind, key=lambda k: -statistics.mean(pss_by_kind[k])):
        c = statistics.mean(cpu_by_kind[k]) if cpu_by_kind[k] else 0
        print(f'   {k:<14} CPU {c:5.0f}%  PSS {statistics.mean(pss_by_kind[k]):6.0f} MB')


if __name__ == '__main__':
    main()
