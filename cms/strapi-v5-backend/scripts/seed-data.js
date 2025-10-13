#!/usr/bin/env node

/**
 * Strapi Content Seeding Script
 * Populates the Strapi CMS with sample blog content for GLAD Labs
 */

const axios = require('axios');

const STRAPI_URL = 'http://localhost:1337';
const API_URL = `${STRAPI_URL}/api`;

// Sample data for GLAD Labs AI-focused content
const sampleData = {
  categories: [
    {
      name: 'AI & Machine Learning',
      slug: 'ai-machine-learning',
    },
    {
      name: 'Game Development',
      slug: 'game-development',
    },
    {
      name: 'Technology Insights',
      slug: 'technology-insights',
    },
    {
      name: 'Business Strategy',
      slug: 'business-strategy',
    },
    {
      name: 'Innovation',
      slug: 'innovation',
    },
  ],

  tags: [
    { name: 'Artificial Intelligence', slug: 'artificial-intelligence' },
    { name: 'Gaming', slug: 'gaming' },
    { name: 'Neural Networks', slug: 'neural-networks' },
    { name: 'Deep Learning', slug: 'deep-learning' },
    { name: 'Computer Vision', slug: 'computer-vision' },
    { name: 'NLP', slug: 'nlp' },
    { name: 'Unity', slug: 'unity' },
    { name: 'Unreal Engine', slug: 'unreal-engine' },
    { name: 'Indie Games', slug: 'indie-games' },
    { name: 'Tech Trends', slug: 'tech-trends' },
    { name: 'Startups', slug: 'startups' },
    { name: 'Digital Transformation', slug: 'digital-transformation' },
  ],

  about: {
    title: 'About Glad Labs',
    subtitle: 'Pioneering the Future of AI-Driven Innovation',
    content: `# Welcome to GLAD Labs

GLAD Labs stands at the forefront of artificial intelligence innovation, where cutting-edge research meets practical application. Founded with a vision to democratize AI technology, we bridge the gap between complex machine learning concepts and real-world solutions that transform industries.

## Our Story

Born from a passion for pushing the boundaries of what's possible with artificial intelligence, GLAD Labs emerged as a response to the growing need for ethical, accessible, and impactful AI solutions. Our journey began with a simple belief: that AI should enhance human potential, not replace it.

## What We Do

### AI Research & Development
We conduct groundbreaking research in machine learning, neural networks, and cognitive computing, translating theoretical breakthroughs into tangible innovations.

### Custom AI Solutions
Our team develops bespoke artificial intelligence systems tailored to specific industry needs, from automated content generation to predictive analytics.

### Game Development Innovation
We explore the intersection of AI and gaming, creating more immersive, adaptive, and intelligent gaming experiences through advanced algorithms.

### Consulting & Strategy
We guide organizations through their AI transformation journey, providing strategic insights and implementation roadmaps for sustainable growth.

## Our Approach

At GLAD Labs, we believe in:
- **Ethical AI Development**: Ensuring our technologies are built with responsibility and transparency
- **Human-Centered Design**: Creating AI that amplifies human capabilities rather than replacing them
- **Open Innovation**: Sharing knowledge and collaborating with the global AI community
- **Practical Implementation**: Focusing on solutions that deliver real-world value

## Join Our Mission

Whether you're a researcher, developer, entrepreneur, or simply curious about the future of AI, GLAD Labs offers a collaborative environment where innovation thrives. Together, we're shaping a future where artificial intelligence serves humanity's greatest aspirations.`,
    mission: `## Our Mission

To democratize artificial intelligence by developing innovative, ethical, and accessible AI solutions that empower individuals and organizations to achieve their fullest potential while fostering a more intelligent and connected world.`,
    vision: `## Our Vision

A future where artificial intelligence seamlessly integrates with human creativity and ingenuity, enabling breakthrough innovations that solve complex global challenges and create unprecedented opportunities for growth, learning, and discovery.`,
    values: `## Our Core Values

### Innovation
We push the boundaries of what's possible, constantly exploring new frontiers in AI research and development.

### Ethics
We prioritize responsible AI development, ensuring our technologies are built with transparency, fairness, and accountability.

### Collaboration
We believe the best innovations emerge from diverse perspectives and open collaboration with the global community.

### Excellence
We strive for the highest standards in everything we do, from research quality to customer service.

### Impact
We focus on creating solutions that make a meaningful difference in people's lives and contribute to positive societal change.

### Accessibility
We work to make advanced AI technologies accessible to organizations of all sizes and individuals from all backgrounds.`,
    team: [
      {
        name: 'Matthew M. Gladding',
        title: 'Founder & CEO',
        bio: 'Visionary leader with expertise in AI research, game development, and entrepreneurship. Matthew founded GLAD Labs with the mission to democratize artificial intelligence and create innovative solutions that bridge the gap between cutting-edge research and practical applications.',
        order: 1,
      },
      {
        name: 'AI Research Team',
        title: 'Research & Development',
        bio: 'Our dedicated team of AI researchers, machine learning engineers, and data scientists work collaboratively to push the boundaries of artificial intelligence and develop breakthrough technologies.',
        order: 2,
      },
    ],
  },

  privacyPolicy: {
    title: 'Privacy Policy',
    lastUpdated: new Date().toISOString(),
    effectiveDate: new Date('2025-01-01').toISOString(),
    contactEmail: 'privacy@gladlabs.com',
    content: `# Privacy Policy

**Effective Date:** January 1, 2025  
**Last Updated:** ${new Date().toLocaleDateString()}

## 1. Introduction

GLAD Labs, LLC ("we," "our," or "us") is committed to protecting your privacy. This Privacy Policy explains how we collect, use, disclose, and safeguard your information when you visit our website, use our services, or interact with our artificial intelligence solutions.

## 2. Information We Collect

### 2.1 Personal Information
We may collect personal information that you voluntarily provide, including:
- Name and contact information (email address, phone number)
- Professional information (company, job title)
- Account credentials and preferences
- Communication content and feedback
- Payment and billing information

### 2.2 Technical Information
We automatically collect certain technical information:
- IP address and device identifiers
- Browser type and version
- Operating system and device information
- Website usage patterns and analytics
- Cookies and similar tracking technologies
- Log files and access records

### 2.3 AI Training Data
When you interact with our AI services:
- Input data and prompts you provide
- Generated outputs and responses
- Usage patterns and preferences
- Performance metrics and feedback

## 3. How We Use Your Information

We use your information for the following purposes:

### 3.1 Service Provision
- Delivering and improving our AI services
- Processing transactions and managing accounts
- Providing customer support and technical assistance
- Customizing user experiences and recommendations

### 3.2 Research and Development
- Training and improving AI models and algorithms
- Conducting research and analytics
- Developing new features and services
- Performance monitoring and optimization

### 3.3 Communications
- Responding to inquiries and requests
- Sending service updates and notifications
- Marketing communications (with consent)
- Legal and regulatory notices

### 3.4 Legal and Security
- Complying with legal obligations
- Protecting against fraud and security threats
- Enforcing our terms of service
- Resolving disputes and legal claims

## 4. Information Sharing and Disclosure

We may share your information in the following circumstances:

### 4.1 Service Providers
We may share information with trusted third-party service providers who assist us in:
- Cloud computing and data storage
- Payment processing and billing
- Analytics and performance monitoring
- Customer support and communications

### 4.2 Business Transfers
In the event of a merger, acquisition, or sale of assets, your information may be transferred as part of the business transaction.

### 4.3 Legal Requirements
We may disclose information when required by law or to:
- Comply with legal processes and government requests
- Protect our rights, property, or safety
- Protect the rights and safety of our users
- Prevent fraud or illegal activities

### 4.4 Aggregated Data
We may share aggregated, de-identified data for research, analytics, and business purposes.

## 5. Data Security

We implement appropriate technical and organizational measures to protect your information:
- Encryption of data in transit and at rest
- Access controls and authentication systems
- Regular security assessments and updates
- Employee training on data protection
- Incident response and breach notification procedures

## 6. AI and Machine Learning Practices

### 6.1 Data Usage in AI Development
- We may use aggregated, anonymized data to train and improve our AI models
- Personal information is de-identified before use in machine learning processes
- We implement privacy-preserving techniques such as differential privacy
- Users can opt out of certain data usage for AI training

### 6.2 AI Transparency
- We strive to make our AI systems interpretable and explainable
- We provide information about how our AI models make decisions
- We regularly audit our AI systems for bias and fairness
- We maintain human oversight of AI-generated content and decisions

## 7. Your Rights and Choices

You have the following rights regarding your personal information:

### 7.1 Access and Portability
- Request access to your personal information
- Receive a copy of your data in a portable format
- Review how your information is being used

### 7.2 Correction and Updates
- Correct inaccurate or incomplete information
- Update your account preferences and settings
- Modify consent preferences

### 7.3 Deletion and Restriction
- Request deletion of your personal information
- Restrict certain processing activities
- Withdraw consent where applicable

### 7.4 Objection and Opt-Out
- Object to certain uses of your information
- Opt out of marketing communications
- Disable cookies and tracking technologies

## 8. International Data Transfers

If you are located outside the United States, please note that we may transfer your information to and process it in the United States and other countries where our service providers operate. We ensure appropriate safeguards are in place for such transfers.

## 9. Children's Privacy

Our services are not intended for children under 13 years of age. We do not knowingly collect personal information from children under 13. If we become aware that we have collected such information, we will take steps to delete it promptly.

## 10. Third-Party Links and Services

Our website may contain links to third-party websites and services. This Privacy Policy does not apply to such third parties, and we encourage you to review their privacy policies.

## 11. Changes to This Privacy Policy

We may update this Privacy Policy from time to time. We will notify you of material changes by:
- Posting the updated policy on our website
- Sending email notifications to registered users
- Providing prominent notice of changes
- Updating the "Last Updated" date

## 12. Contact Information

If you have questions, concerns, or requests regarding this Privacy Policy or our privacy practices, please contact us:

**GLAD Labs, LLC**  
**Privacy Officer**  
Email: privacy@gladlabs.com  
Website: https://gladlabs.com/contact  

For data protection inquiries in the European Union, you may also contact your local data protection authority.

## 13. California Privacy Rights

If you are a California resident, you have additional rights under the California Consumer Privacy Act (CCPA):
- Right to know what personal information is collected
- Right to delete personal information
- Right to opt out of the sale of personal information
- Right to non-discrimination for exercising privacy rights

To exercise these rights, please contact us using the information provided above.

## 14. Retention Policy

We retain personal information for as long as necessary to:
- Provide our services and fulfill contractual obligations
- Comply with legal and regulatory requirements
- Resolve disputes and enforce our agreements
- Pursue legitimate business interests

We regularly review and delete information that is no longer needed.

---

*This Privacy Policy is part of our commitment to transparency and responsible data handling. We encourage you to review this policy regularly and contact us with any questions or concerns.*`,
  },

  authors: [
    {
      name: 'Matthew M. Gladding',
      bio: 'Founder and CEO of GLAD Labs, AI researcher and game development enthusiast.',
    },
    {
      name: 'AI Research Team',
      bio: 'Collective insights from GLAD Labs AI research and development team.',
    },
  ],

  posts: [
    {
      title: 'The Future of AI in Game Development',
      slug: 'future-ai-game-development',
      excerpt:
        'Exploring how artificial intelligence is revolutionizing the gaming industry and creating more immersive player experiences.',
      content: `# The Future of AI in Game Development

Artificial Intelligence is transforming the gaming industry in unprecedented ways. From procedural content generation to intelligent NPCs, AI is enabling developers to create richer, more dynamic gaming experiences.

## Key Areas of AI Innovation

### 1. Procedural Content Generation
AI algorithms can generate vast, diverse game worlds automatically, reducing development time while increasing content variety.

### 2. Intelligent NPCs
Modern AI enables non-player characters to exhibit more realistic behaviors and adapt to player actions in real-time.

### 3. Personalized Gaming Experiences
Machine learning algorithms analyze player behavior to customize difficulty levels and content recommendations.

## The GLAD Labs Approach

At GLAD Labs, we're pushing the boundaries of what's possible with AI in gaming. Our research focuses on:

- **Adaptive Storytelling**: Using AI to create branching narratives that respond to player choices
- **Emotion Recognition**: Implementing computer vision to detect player emotions and adjust gameplay
- **Procedural Music Generation**: Creating dynamic soundtracks that evolve with gameplay

## Looking Ahead

The future of AI in gaming is bright. As we continue to advance these technologies, we can expect even more immersive and personalized gaming experiences that blur the line between virtual and reality.`,
      date: new Date('2025-01-15').toISOString(),
      categorySlug: 'ai-machine-learning',
      tagSlugs: ['artificial-intelligence', 'gaming', 'tech-trends'],
    },
    {
      title: 'Building Neural Networks for Computer Vision',
      slug: 'building-neural-networks-computer-vision',
      excerpt:
        'A comprehensive guide to implementing convolutional neural networks for image recognition and computer vision applications.',
      content: `# Building Neural Networks for Computer Vision

Computer vision represents one of the most exciting frontiers in artificial intelligence. This guide explores the fundamental concepts and practical implementation of neural networks for visual recognition tasks.

## Understanding Convolutional Neural Networks

CNNs are the backbone of modern computer vision systems. They excel at recognizing patterns and features in images through:

- **Convolutional layers** that detect local features
- **Pooling layers** that reduce spatial dimensions
- **Fully connected layers** that perform classification

## Practical Implementation

### Data Preparation
The quality of your training data directly impacts model performance. Key considerations include:
- Dataset diversity and balance
- Image preprocessing and augmentation
- Proper train/validation/test splits

### Model Architecture
Popular architectures like ResNet, VGG, and EfficientNet provide excellent starting points for most computer vision tasks.

### Training Strategies
- Transfer learning from pre-trained models
- Progressive resizing for faster convergence
- Learning rate scheduling and regularization

## Real-World Applications

Computer vision powers numerous applications:
- **Autonomous vehicles** for object detection and navigation
- **Medical imaging** for diagnostic assistance
- **Retail** for inventory management and customer analytics
- **Gaming** for gesture recognition and AR experiences

## Getting Started

Ready to dive into computer vision? Start with these steps:
1. Choose a framework (TensorFlow, PyTorch)
2. Begin with a simple image classification project
3. Experiment with pre-trained models
4. Gradually increase complexity as you gain experience

The field of computer vision continues to evolve rapidly, offering endless opportunities for innovation and discovery.`,
      date: new Date('2025-02-01').toISOString(),
      categorySlug: 'ai-machine-learning',
      tagSlugs: ['neural-networks', 'computer-vision', 'deep-learning'],
    },
    {
      title: 'Unity vs Unreal Engine: Choosing the Right Platform',
      slug: 'unity-vs-unreal-engine-choosing-platform',
      excerpt:
        'An in-depth comparison of Unity and Unreal Engine to help developers choose the best game development platform for their projects.',
      content: `# Unity vs Unreal Engine: Choosing the Right Platform

Selecting the right game engine is crucial for project success. This comprehensive comparison examines Unity and Unreal Engine across multiple dimensions to help you make an informed decision.

## Overview

Both Unity and Unreal Engine are powerful, industry-standard game development platforms, but they serve different needs and development styles.

### Unity Strengths
- **Accessibility**: Lower learning curve, especially for beginners
- **Cross-platform support**: Deploy to 25+ platforms with minimal changes
- **Asset Store**: Vast marketplace of ready-made assets and tools
- **2D excellence**: Superior 2D game development tools
- **Scripting flexibility**: C# scripting with excellent IDE integration

### Unreal Engine Strengths
- **Visual quality**: Industry-leading graphics and rendering capabilities
- **Blueprint system**: Visual scripting that enables rapid prototyping
- **Performance**: Optimized for high-end, AAA game development
- **Built-in tools**: Comprehensive suite of development and debugging tools
- **Pricing model**: Free until you earn $1M in revenue

## Technical Considerations

### Performance
- **Unity**: Excellent for mobile and indie games, scalable performance
- **Unreal**: Optimized for high-end PC and console games

### Learning Curve
- **Unity**: Gentler introduction, extensive documentation and tutorials
- **Unreal**: Steeper learning curve but powerful once mastered

### Community and Support
- **Unity**: Large community, extensive third-party resources
- **Unreal**: Strong community, excellent official documentation

## Making Your Decision

Choose **Unity** if you're:
- Developing mobile or 2D games
- Working on indie projects with smaller teams
- New to game development
- Prioritizing rapid deployment across multiple platforms

Choose **Unreal Engine** if you're:
- Creating high-fidelity 3D games
- Developing for PC/console markets
- Working on AAA or visually intensive projects
- Comfortable with complex development environments

## The GLAD Labs Perspective

At GLAD Labs, we use both engines depending on project requirements. Unity excels for our AI research prototypes and mobile experiments, while Unreal Engine powers our high-fidelity demonstration projects.

The best engine is the one that aligns with your project goals, team expertise, and target platforms. Consider starting small with either platform to gain hands-on experience before committing to larger projects.`,
      date: new Date('2025-02-10').toISOString(),
      categorySlug: 'game-development',
      tagSlugs: ['unity', 'unreal-engine', 'game-development'],
    },
    {
      title: 'The Rise of Indie Game Development',
      slug: 'rise-indie-game-development',
      excerpt:
        'Exploring the independent game development scene and how small teams are creating innovative experiences that compete with major studios.',
      content: `# The Rise of Indie Game Development

The indie game development scene has exploded in recent years, with small teams and solo developers creating innovative experiences that often outshine major studio productions.

## The Indie Advantage

Independent developers possess unique advantages:

### Creative Freedom
- No corporate constraints on artistic vision
- Ability to experiment with unconventional gameplay
- Direct connection with player communities
- Rapid iteration and adaptation

### Market Accessibility
- Digital distribution platforms (Steam, itch.io, mobile stores)
- Lower barriers to entry
- Crowdfunding opportunities
- Social media marketing reach

## Success Stories

Notable indie success stories demonstrate the potential:
- **Stardew Valley**: One-person development team, massive commercial success
- **Among Us**: Small team creation that became a global phenomenon
- **Hollow Knight**: Kickstarted project that rivals AAA productions
- **Celeste**: Critically acclaimed platformer with innovative mechanics

## Challenges and Solutions

### Resource Constraints
- **Challenge**: Limited budget and team size
- **Solution**: Focus on core gameplay mechanics, leverage free/affordable tools

### Marketing and Visibility
- **Challenge**: Standing out in crowded marketplace
- **Solution**: Build community early, authentic social media presence

### Technical Limitations
- **Challenge**: Lack of specialized expertise
- **Solution**: Use accessible engines like Unity, leverage online learning resources

## Tools for Indie Success

Modern development tools democratize game creation:
- **Game Engines**: Unity, Godot, GameMaker Studio
- **Art Tools**: Aseprite, Blender, GIMP
- **Audio**: Audacity, FMOD
- **Version Control**: Git, GitHub
- **Marketing**: Discord, Twitter, TikTok

## The Future of Indie Development

Trends shaping indie game development:
- **AI-Assisted Development**: Tools for art generation, code assistance
- **Cross-Platform Development**: Easier multi-platform releases
- **Community-Driven Development**: Early access and player feedback integration
- **Sustainable Development**: Focus on developer wellbeing and long-term careers

## Getting Started

For aspiring indie developers:
1. Start small with simple projects
2. Join game development communities
3. Participate in game jams
4. Build a portfolio of completed projects
5. Learn from both successes and failures

The indie game development scene continues to thrive, offering opportunities for creative expression and commercial success. With passion, persistence, and the right tools, anyone can create compelling gaming experiences.`,
      date: new Date('2025-02-15').toISOString(),
      categorySlug: 'game-development',
      tagSlugs: ['indie-games', 'game-development', 'startups'],
    },
    {
      title: 'Digital Transformation in the Modern Enterprise',
      slug: 'digital-transformation-modern-enterprise',
      excerpt:
        'How businesses are leveraging technology to reimagine operations, customer experiences, and competitive advantages in the digital age.',
      content: `# Digital Transformation in the Modern Enterprise

Digital transformation has evolved from a buzzword to a business imperative. Organizations across industries are reimagining their operations, customer experiences, and business models through strategic technology adoption.

## Understanding Digital Transformation

Digital transformation encompasses:
- **Process automation** and optimization
- **Data-driven decision making** capabilities
- **Customer experience** enhancement
- **Business model** innovation
- **Cultural and organizational** change

## Key Technology Enablers

### Cloud Computing
- Scalable infrastructure without capital investment
- Enhanced collaboration and remote work capabilities
- Improved disaster recovery and business continuity

### Artificial Intelligence
- Automated customer service through chatbots
- Predictive analytics for business intelligence
- Process optimization and anomaly detection

### Internet of Things (IoT)
- Real-time monitoring and data collection
- Predictive maintenance capabilities
- Enhanced supply chain visibility

### Blockchain Technology
- Improved transaction security and transparency
- Streamlined verification processes
- Enhanced trust in digital interactions

## Implementation Strategies

### Assessment and Planning
1. **Current state analysis**: Evaluate existing processes and technologies
2. **Vision definition**: Establish clear digital transformation goals
3. **Roadmap creation**: Prioritize initiatives based on impact and feasibility

### Technology Integration
- Start with pilot projects to prove value
- Ensure seamless integration with existing systems
- Invest in employee training and change management

### Cultural Transformation
- Foster innovation and experimentation
- Encourage cross-functional collaboration
- Develop digital literacy across the organization

## Measuring Success

Key performance indicators for digital transformation:
- **Operational efficiency** metrics
- **Customer satisfaction** improvements
- **Revenue growth** from digital channels
- **Employee engagement** and productivity
- **Time-to-market** for new products/services

## Common Challenges

### Resistance to Change
- **Solution**: Comprehensive change management and communication
- **Approach**: Involve employees in the transformation process

### Legacy System Integration
- **Solution**: Gradual migration strategies and API-first approaches
- **Approach**: Modernize incrementally rather than wholesale replacement

### Skills Gap
- **Solution**: Investment in training and strategic hiring
- **Approach**: Partner with educational institutions and technology providers

## Future Trends

Emerging trends in digital transformation:
- **Edge computing** for real-time processing
- **Augmented and virtual reality** for training and visualization
- **Quantum computing** for complex problem solving
- **Sustainable technology** for environmental responsibility

## The GLAD Labs Approach

At GLAD Labs, we help organizations navigate digital transformation through:
- **AI strategy consulting** and implementation
- **Custom software development** for unique business needs
- **Data analytics** and business intelligence solutions
- **Training and support** for technology adoption

Digital transformation is not a destination but a continuous journey of innovation and adaptation. Organizations that embrace this mindset will thrive in the digital economy.`,
      date: new Date('2025-02-20').toISOString(),
      categorySlug: 'business-strategy',
      tagSlugs: ['digital-transformation', 'tech-trends', 'startups'],
    },
  ],
};

// Helper function to make API requests
async function apiRequest(method, endpoint, data = null) {
  try {
    const config = {
      method,
      url: `${API_URL}${endpoint}`,
      headers: {
        'Content-Type': 'application/json',
      },
    };

    if (data) {
      config.data = data;
    }

    const response = await axios(config);
    return response.data;
  } catch (error) {
    console.error(
      `API request failed: ${method} ${endpoint}`,
      error.response?.data || error.message
    );
    return null;
  }
}

// Create categories
async function createCategories() {
  console.log('Creating categories...');
  const createdCategories = {};

  for (const category of sampleData.categories) {
    const result = await apiRequest('POST', '/categories', {
      data: {
        ...category,
        publishedAt: new Date().toISOString(),
      },
    });

    if (result) {
      createdCategories[category.slug] = result.data;
      console.log(`✓ Created category: ${category.name}`);
    }
  }

  return createdCategories;
}

// Create tags
async function createTags() {
  console.log('Creating tags...');
  const createdTags = {};

  for (const tag of sampleData.tags) {
    const result = await apiRequest('POST', '/tags', {
      data: {
        ...tag,
        publishedAt: new Date().toISOString(),
      },
    });

    if (result) {
      createdTags[tag.slug] = result.data;
      console.log(`✓ Created tag: ${tag.name}`);
    }
  }

  return createdTags;
}

// Create authors
async function createAuthors() {
  console.log('Creating authors...');
  const createdAuthors = {};

  for (const author of sampleData.authors) {
    const result = await apiRequest('POST', '/authors', {
      data: {
        ...author,
        publishedAt: new Date().toISOString(),
      },
    });

    if (result) {
      createdAuthors[author.name] = result.data;
      console.log(`✓ Created author: ${author.name}`);
    }
  }

  return createdAuthors;
}

// Create posts
async function createPosts(categories, tags, authors) {
  console.log('Creating posts...');

  for (const post of sampleData.posts) {
    const category = categories[post.categorySlug];
    const postTags = post.tagSlugs
      .map((slug) => tags[slug]?.id)
      .filter(Boolean);
    const author = Object.values(authors)[0]; // Use first author for all posts

    const postData = {
      title: post.title,
      slug: post.slug,
      content: post.content,
      excerpt: post.excerpt,
      date: post.date,
      publishedAt: new Date().toISOString(),
      category: category?.id,
      tags: postTags,
      author: author?.id,
    };

    const result = await apiRequest('POST', '/posts', { data: postData });

    if (result) {
      console.log(`✓ Created post: ${post.title}`);
    }
  }
}

// Create About page
async function createAbout() {
  console.log('Creating About page...');

  const aboutData = {
    ...sampleData.about,
    publishedAt: new Date().toISOString(),
  };

  const result = await apiRequest('PUT', '/about', { data: aboutData });

  if (result) {
    console.log('✓ Created About page');
  }

  return result?.data;
}

// Create Privacy Policy page
async function createPrivacyPolicy() {
  console.log('Creating Privacy Policy page...');

  const privacyData = {
    ...sampleData.privacyPolicy,
    publishedAt: new Date().toISOString(),
  };

  const result = await apiRequest('PUT', '/privacy-policy', {
    data: privacyData,
  });

  if (result) {
    console.log('✓ Created Privacy Policy page');
  }

  return result?.data;
}

// Main seeding function
async function seedData() {
  console.log('🌱 Starting Strapi content seeding...');
  console.log(`📡 Strapi URL: ${STRAPI_URL}`);

  try {
    // Check if Strapi is running
    await axios.get(`${STRAPI_URL}/_health`);
    console.log('✓ Strapi is running');

    // Create content in order
    const categories = await createCategories();
    const tags = await createTags();
    const authors = await createAuthors();
    await createPosts(categories, tags, authors);
    await createAbout();
    await createPrivacyPolicy();

    console.log('🎉 Content seeding completed successfully!');
    console.log(`📖 Visit ${STRAPI_URL}/admin to manage your content`);
    console.log(`🌐 API available at ${API_URL}`);
  } catch (error) {
    console.error('❌ Seeding failed:', error.message);
    console.log('Make sure Strapi is running on http://localhost:1337');
  }
}

// Run the seeding script
if (require.main === module) {
  seedData();
}

module.exports = { seedData };
