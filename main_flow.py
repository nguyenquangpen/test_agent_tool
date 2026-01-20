from cwagent.ais.agent_run import Agent
import asyncio

async def main():
    agent = Agent(test_uuid="test-123", auth_token="your-token")
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())