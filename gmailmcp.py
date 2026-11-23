import asyncio
import httpx2
import nest_asyncio
from mcp.client.streamable_http import streamable_http_client
from mcp import ClientSession

# The notebook already runs an event loop, so asyncio.run() needs nest_asyncio.
nest_asyncio.apply()
DATABRICKS_TOKEN = dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()

async def main():
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {DATABRICKS_TOKEN}"})
    async with http_client, streamable_http_client(
        "https://dbc-870c40d6-c4b7.cloud.databricks.com/ai-gateway/mcp-services/system.ai.gmail", http_client=http_client
    ) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            for tool in result.tools:
                print(f"{tool.name}: {tool.description}")

asyncio.run(main())