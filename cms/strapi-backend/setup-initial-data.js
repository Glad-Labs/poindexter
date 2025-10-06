/**
 * Initial data setup script for GLAD Labs Strapi CMS
 * Creates categories, tags, and authors aligned with AI Development/Video Gaming focus
 */

const fetch = require("node-fetch");

const baseURL = "http://localhost:1337/api";

async function setupInitialData() {
  try {
    console.log("🚀 Setting up initial data for GLAD Labs CMS...\n");

    // Create Categories
    const categories = [
      { Name: "AI Development", Slug: "ai-development" },
      { Name: "Game Design", Slug: "game-design" },
      { Name: "Unity Engine", Slug: "unity-engine" },
      { Name: "Machine Learning", Slug: "machine-learning" },
      { Name: "Tech Tutorial", Slug: "tech-tutorial" },
    ];

    console.log("📁 Creating categories...");
    for (const category of categories) {
      try {
        const response = await fetch(`${baseURL}/categories`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ data: category }),
        });
        if (response.ok) {
          console.log(`  ✅ Created category: ${category.Name}`);
        } else {
          console.log(`  ⚠️  Category ${category.Name} might already exist`);
        }
      } catch (error) {
        console.log(`  ⚠️  Category ${category.Name} might already exist`);
      }
    }

    // Create Tags
    const tags = [
      {
        Name: "Python",
        Slug: "python",
        Color: "#3776ab",
        Description: "Python programming language",
      },
      {
        Name: "C#",
        Slug: "csharp",
        Color: "#239120",
        Description: "C# programming for Unity and .NET",
      },
      {
        Name: "Neural Networks",
        Slug: "neural-networks",
        Color: "#ff6b35",
        Description: "Deep learning and neural network architectures",
      },
      {
        Name: "Game Development",
        Slug: "game-development",
        Color: "#8b5cf6",
        Description: "Video game creation and development",
      },
      {
        Name: "Automation",
        Slug: "automation",
        Color: "#10b981",
        Description: "Process automation and AI agents",
      },
      {
        Name: "Tutorial",
        Slug: "tutorial",
        Color: "#f59e0b",
        Description: "Step-by-step learning content",
      },
    ];

    console.log("\n🏷️  Creating tags...");
    for (const tag of tags) {
      try {
        const response = await fetch(`${baseURL}/tags`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ data: tag }),
        });
        if (response.ok) {
          console.log(`  ✅ Created tag: ${tag.Name}`);
        } else {
          console.log(`  ⚠️  Tag ${tag.Name} might already exist`);
        }
      } catch (error) {
        console.log(`  ⚠️  Tag ${tag.Name} might already exist`);
      }
    }

    // Create Authors
    const authors = [
      {
        Name: "Matthew M. Gladding",
        Bio: "Founder of Glad Labs LLC. Expert in AI automation, game development, and building scalable digital systems.",
        Email: "matt@gladlabs.ai",
        IsAIAgent: false,
      },
      {
        Name: "Content Agent",
        Bio: "AI-powered content generation agent specializing in technical articles about AI development and game design.",
        IsAIAgent: true,
        AgentVersion: "v1.0.0",
      },
    ];

    console.log("\n👤 Creating authors...");
    for (const author of authors) {
      try {
        const response = await fetch(`${baseURL}/authors`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ data: author }),
        });
        if (response.ok) {
          console.log(`  ✅ Created author: ${author.Name}`);
        } else {
          console.log(`  ⚠️  Author ${author.Name} might already exist`);
        }
      } catch (error) {
        console.log(`  ⚠️  Author ${author.Name} might already exist`);
      }
    }

    console.log("\n🎉 Initial data setup complete!");
    console.log(
      "📊 You can now access the Strapi admin at: http://localhost:1337/admin"
    );
  } catch (error) {
    console.error("❌ Error setting up initial data:", error);
  }
}

setupInitialData();
