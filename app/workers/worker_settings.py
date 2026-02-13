"""
Market2Agent - Worker Settings (Updated)

This file replaces or extends your existing arq WorkerSettings.
It adds:
    1. execute_agent job function (agent runner)
    2. Scheduler tick as a cron job (every 15 minutes)

Your existing run_audit function stays as-is.
"""
from arq import cron
from arq.connections import RedisSettings

from app.config import settings

REDIS_SETTINGS = RedisSettings.from_dsn(settings.REDIS_URL)


# Import job functions
# These are your existing functions — keep them
# from app.worker import run_audit

# New: agent execution function
from app.workers.agent_runner import execute_agent

# New: scheduler tick
from app.agents.scheduler import tick as scheduler_tick


class WorkerSettings:
    """
    arq worker configuration.
    
    Merge this with your existing WorkerSettings.
    Keep your existing run_audit function in the functions list.
    """
    
    functions = [
        # Your existing audit job:
        # run_audit,
        
        # New: per-agent execution
        execute_agent,
    ]
    
    # Scheduled jobs
    cron_jobs = [
        # Agent scheduler - runs every 15 minutes
        # Checks which agents need to execute and queues them
        cron(
            scheduler_tick,
            minute={0, 15, 30, 45},  # Every 15 minutes
            unique=True,  # Prevent duplicate runs
        ),
    ]
    
    redis_settings = REDIS_SETTINGS
    max_jobs = 10
    job_timeout = 300  # 5 minute timeout per job


# ===========================================
# INTEGRATION INSTRUCTIONS
# ===========================================
#
# In your existing worker.py or WorkerSettings, add:
#
#   from app.workers.agent_runner import execute_agent
#   from app.agents.scheduler import tick as scheduler_tick
#   from arq import cron
#
# Then update your WorkerSettings:
#
#   class WorkerSettings:
#       functions = [run_audit, execute_agent]  # Add execute_agent
#       cron_jobs = [
#           cron(scheduler_tick, minute={0, 15, 30, 45}, unique=True),
#       ]
#
# That's it. The existing run_audit stays. The scheduler
# tick runs every 15 minutes alongside your existing worker.
