/**
 * Sample content creation script for GLAD Labs
 * Creates a test blog post using the content agent structure
 */

const fetch = require('node-fetch');

const baseURL = 'http://localhost:1337/api';

async function createSamplePost() {
  try {
    console.log('📝 Creating sample blog post...\n');

    // Get author ID (Content Agent)
    let authorResponse = await fetch(
      `${baseURL}/authors?filters[Name][$eq]=Content Agent`
    );
    let authorData = await authorResponse.json();
    const authorId = authorData.data[0]?.id;

    // Get category ID (AI Development)
    let categoryResponse = await fetch(
      `${baseURL}/categories?filters[Name][$eq]=AI Development`
    );
    let categoryData = await categoryResponse.json();
    const categoryId = categoryData.data[0]?.id;

    // Get some tag IDs
    let pythonTagResponse = await fetch(
      `${baseURL}/tags?filters[Name][$eq]=Python`
    );
    let pythonTagData = await pythonTagResponse.json();
    const pythonTagId = pythonTagData.data[0]?.id;

    let tutorialTagResponse = await fetch(
      `${baseURL}/tags?filters[Name][$eq]=Tutorial`
    );
    let tutorialTagData = await tutorialTagResponse.json();
    const tutorialTagId = tutorialTagData.data[0]?.id;

    const samplePost = {
      Title: 'Building AI Agents with Python: A GLAD Labs Guide',
      Slug: 'building-ai-agents-python-glad-labs-guide',
      MetaDescription:
        'Learn how to build intelligent AI agents using Python and modern frameworks. A comprehensive guide from GLAD Labs covering CrewAI, LangChain, and automated content generation.',
      BodyContent: [
        {
          type: 'paragraph',
          children: [
            {
              type: 'text',
              text: "# Building AI Agents with Python: A GLAD Labs Guide\\n\\nIn the rapidly evolving landscape of artificial intelligence, the ability to create autonomous agents that can think, reason, and act has become increasingly valuable. At GLAD Labs, we've been pushing the boundaries of what's possible with AI-driven automation, and today we're sharing our insights on building intelligent agents using Python.\\n\\n## The Foundation: Understanding AI Agents\\n\\nAI agents are autonomous systems that can perceive their environment, make decisions, and take actions to achieve specific goals. Unlike traditional software that follows predetermined paths, AI agents can adapt, learn, and respond to new situations in real-time.\\n\\n### Key Components of Modern AI Agents\\n\\n1. **Perception Layer**: How agents understand their environment\\n2. **Decision Engine**: The neural network that processes information\\n3. **Action Framework**: How agents execute their decisions\\n4. **Memory System**: Learning and adaptation mechanisms\\n\\n## Python: The Language of Choice\\n\\nPython has emerged as the dominant language for AI development, and for good reason:\\n\\n- **Rich Ecosystem**: Libraries like CrewAI, LangChain, and TensorFlow\\n- **Simplicity**: Clean syntax that allows focus on logic over syntax\\n- **Community**: Vast resources and collaborative development\\n- **Integration**: Seamless connection with cloud services and APIs\\n\\n## Building Your First Agent\\n\\nLet's walk through creating a simple content analysis agent:\\n\\n```python\\nfrom crewai import Agent, Task, Crew\\nfrom langchain.llms import OpenAI\\n\\n# Define the agent\\nanalyst = Agent(\\n    role='Content Analyst',\\n    goal='Analyze and improve content quality',\\n    backstory='You are an expert content analyst...',\\n    llm=OpenAI(temperature=0.1)\\n)\\n\\n# Create a task\\nanalysis_task = Task(\\n    description='Analyze the given content for quality and engagement',\\n    agent=analyst\\n)\\n\\n# Form the crew\\ncrew = Crew(\\n    agents=[analyst],\\n    tasks=[analysis_task]\\n)\\n```\\n\\n## Advanced Techniques\\n\\n### Iterative Self-Critique\\n\\nOne of the most powerful patterns we've implemented at GLAD Labs is the iterative self-critique loop. This allows agents to review and improve their own work:\\n\\n```python\\ndef iterative_improvement(content, max_iterations=3):\\n    for i in range(max_iterations):\\n        feedback = critic_agent.analyze(content)\\n        if feedback.score > 0.9:\\n            break\\n        content = improver_agent.enhance(content, feedback)\\n    return content\\n```\\n\\n### Multi-Agent Collaboration\\n\\nReal-world applications often require multiple specialized agents working together:\\n\\n- **Research Agent**: Gathers information from various sources\\n- **Creative Agent**: Generates original content\\n- **Quality Agent**: Reviews and validates output\\n- **Publishing Agent**: Handles final formatting and distribution\\n\\n## Practical Applications\\n\\nAt GLAD Labs, we've successfully deployed AI agents for:\\n\\n1. **Automated Content Generation**: Blog posts, technical documentation\\n2. **Code Review and Optimization**: Intelligent code analysis\\n3. **Market Research**: Real-time trend analysis\\n4. **Customer Support**: Intelligent ticket routing and response\\n\\n## Best Practices\\n\\n### 1. Start Simple\\nBegin with single-purpose agents before building complex multi-agent systems.\\n\\n### 2. Design for Observability\\nImplement comprehensive logging and monitoring from day one.\\n\\n### 3. Embrace Failure\\nBuild robust error handling and recovery mechanisms.\\n\\n### 4. Iterate Rapidly\\nUse feedback loops to continuously improve agent performance.\\n\\n## The Future of AI Agents\\n\\nAs we look toward the future, several trends are shaping the evolution of AI agents:\\n\\n- **Increased Autonomy**: Agents will require less human oversight\\n- **Better Integration**: Seamless connection with existing systems\\n- **Specialized Intelligence**: Domain-specific expertise\\n- **Collaborative Networks**: Agents working together across organizations\\n\\n## Conclusion\\n\\nBuilding AI agents with Python is not just about writing code—it's about creating intelligent systems that can augment human capabilities and automate complex workflows. The tools and frameworks available today make it possible for developers to create sophisticated agents that were unimaginable just a few years ago.\\n\\nAt GLAD Labs, we believe that the future belongs to those who can successfully combine human creativity with artificial intelligence. By mastering the art of AI agent development, you're not just learning to code—you're learning to architect the future.\\n\\n## Get Started Today\\n\\nReady to begin your journey into AI agent development? Start with the basics, experiment with small projects, and gradually build more complex systems. Remember, every expert was once a beginner, and the best time to start is now.\\n\\n*This article is part of the GLAD Labs knowledge series on AI development and automation. For more insights and tutorials, follow our blog and join our community of AI developers pushing the boundaries of what's possible.*",
            },
          ],
        },
      ],
      Keywords:
        'Python, AI Agents, CrewAI, LangChain, Automation, Machine Learning',
      ReadingTime: 8,
      Excerpt:
        'Learn how to build intelligent AI agents using Python and modern frameworks. A comprehensive guide covering CrewAI, LangChain, and automated content generation for real-world applications.',
      author: authorId,
      category: categoryId,
      tags: [pythonTagId, tutorialTagId].filter(Boolean),
    };

    console.log('📤 Publishing sample post to Strapi...');
    const response = await fetch(`${baseURL}/posts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: samplePost }),
    });

    if (response.ok) {
      const result = await response.json();
      console.log(
        `✅ Successfully created sample post with ID: ${result.data.id}`
      );
      console.log(
        `🔗 Post URL: http://localhost:1337/admin/content-manager/collectionType/api::post.post/${result.data.id}`
      );

      // Create content metrics for this post
      const metricsData = {
        Views: 0,
        Likes: 0,
        Shares: 0,
        Comments: 0,
        EngagementRate: 0.0,
        AgentVersion: 'v1.0.0',
        GenerationTimeMs: 2500,
        post: result.data.id,
      };

      const metricsResponse = await fetch(`${baseURL}/content-metrics`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data: metricsData }),
      });

      if (metricsResponse.ok) {
        console.log('📊 Created content metrics for the sample post');
      }
    } else {
      const error = await response.text();
      console.error(`❌ Failed to create sample post: ${error}`);
    }
  } catch (error) {
    console.error('❌ Error creating sample content:', error);
  }
}

createSamplePost();
