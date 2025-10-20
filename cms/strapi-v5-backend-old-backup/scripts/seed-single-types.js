#!/usr/bin/env node

/**
 * Simple Strapi Single Type Content Seeder
 * Seeds only the About and Privacy Policy single types
 */

const axios = require('axios');

const STRAPI_URL = 'http://localhost:1337';
const API_URL = `${STRAPI_URL}/api`;

// Sample About page data
const aboutData = {
  title: 'About GLAD Labs',
  subtitle: 'Building the Future of AI-Powered Innovation',
  description:
    'GLAD Labs is a cutting-edge AI development company specializing in innovative solutions that bridge the gap between artificial intelligence and practical applications. We transform ideas into intelligent systems that drive business growth and technological advancement.',
  mission:
    'To democratize artificial intelligence by creating accessible, powerful, and ethical AI solutions that enhance human capabilities and drive positive change across industries.',
  vision:
    'A future where AI seamlessly integrates into everyday life, empowering individuals and organizations to achieve unprecedented levels of innovation and efficiency.',
  values: [
    "Innovation: We push the boundaries of what's possible with AI technology",
    'Ethics: We prioritize responsible AI development and deployment',
    'Collaboration: We believe in the power of teamwork and diverse perspectives',
    'Excellence: We strive for the highest quality in everything we do',
    'Transparency: We maintain open and honest communication with all stakeholders',
  ],
  teamMembers: [
    {
      name: 'Dr. Sarah Chen',
      role: 'CEO & Co-Founder',
      bio: 'PhD in Computer Science from MIT, former Google AI researcher with 10+ years experience in machine learning and neural networks.',
      image: null,
    },
    {
      name: 'Marcus Rodriguez',
      role: 'CTO & Co-Founder',
      bio: 'Former Amazon Web Services architect with expertise in cloud infrastructure and scalable AI systems.',
      image: null,
    },
    {
      name: 'Dr. Emily Watson',
      role: 'Head of Research',
      bio: 'PhD in Cognitive Science, specializing in natural language processing and human-AI interaction.',
      image: null,
    },
    {
      name: 'Alex Thompson',
      role: 'Lead Game Developer',
      bio: 'Award-winning game developer with 8+ years experience in Unity and Unreal Engine, specializing in AI-driven game mechanics.',
      image: null,
    },
  ],
  foundedYear: 2020,
  headquarters: 'San Francisco, CA',
  employees: '25-50',
  seo: {
    metaTitle: 'About GLAD Labs - AI Innovation Company',
    metaDescription:
      'Learn about GLAD Labs, a leading AI development company creating innovative solutions that bridge artificial intelligence and practical applications.',
    keywords:
      'AI company, artificial intelligence, machine learning, innovation, technology, San Francisco',
  },
};

// Sample Privacy Policy data
const privacyPolicyData = {
  title: 'Privacy Policy',
  effectiveDate: '2024-01-01',
  lastUpdated: new Date().toISOString().split('T')[0],
  content: `## Privacy Policy for GLAD Labs

### 1. Information We Collect

**Personal Information:** When you interact with our services, we may collect personal information such as:
- Name and contact information
- Email addresses
- Professional information
- Account credentials
- Payment information (processed securely through third-party providers)

**Usage Data:** We automatically collect information about how you use our services:
- Device information and identifiers
- IP addresses and location data
- Browser type and version
- Pages visited and time spent
- Interaction patterns and preferences

**Cookies and Tracking:** We use cookies and similar technologies to:
- Enhance user experience
- Analyze usage patterns
- Provide personalized content
- Maintain session security

### 2. How We Use Your Information

We use collected information to:
- Provide and improve our AI services
- Personalize user experience
- Communicate about our services
- Process transactions securely
- Comply with legal obligations
- Protect against fraud and security threats

### 3. Information Sharing

We do not sell personal information. We may share information with:
- Service providers and business partners
- Legal authorities when required by law
- Third parties with your explicit consent
- In connection with business transactions

### 4. Data Security

We implement robust security measures:
- Encryption of data in transit and at rest
- Regular security audits and assessments
- Access controls and authentication
- Employee training on data protection
- Incident response procedures

### 5. Your Rights

Depending on your location, you may have rights to:
- Access your personal information
- Correct inaccurate data
- Delete your information
- Restrict processing
- Data portability
- Object to certain processing

**California Residents (CCPA):** You have additional rights including the right to know what personal information is collected and the right to opt-out of the sale of personal information.

**EU Residents (GDPR):** You have rights under the General Data Protection Regulation including the right to withdraw consent and lodge complaints with supervisory authorities.

### 6. Data Retention

We retain information only as long as necessary for:
- Providing our services
- Legal compliance
- Dispute resolution
- Legitimate business purposes

### 7. International Transfers

Your information may be transferred to and processed in countries other than your country of residence. We ensure appropriate safeguards are in place.

### 8. Children's Privacy

Our services are not intended for children under 13. We do not knowingly collect personal information from children under 13.

### 9. Changes to Privacy Policy

We may update this privacy policy periodically. We will notify you of material changes through our services or other communication methods.

### 10. Contact Information

For privacy-related questions or requests, contact us at:
- Email: privacy@gladlabs.ai
- Address: GLAD Labs Privacy Officer, 123 Innovation Drive, San Francisco, CA 94105
- Phone: (555) 123-4567

### 11. Third-Party Services

Our services may integrate with third-party platforms. This privacy policy does not cover third-party practices. Please review their privacy policies separately.

### 12. Automated Decision Making

We may use automated decision-making processes, including AI algorithms. You have the right to request human review of automated decisions that significantly affect you.`,
  seo: {
    metaTitle: 'Privacy Policy - GLAD Labs',
    metaDescription:
      'Read GLAD Labs privacy policy to understand how we collect, use, and protect your personal information.',
    keywords:
      'privacy policy, data protection, CCPA, GDPR, personal information',
  },
};

// API request helper
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
    console.log(
      `API request failed: ${method} ${endpoint}`,
      error.response?.data || error.message
    );
    return null;
  }
}

// Health check
async function checkStrapi() {
  try {
    await axios.get(`${STRAPI_URL}/_health`);
    console.log('✓ Strapi is running');
    return true;
  } catch (error) {
    console.log('❌ Strapi is not running');
    console.log('Make sure Strapi is running on', STRAPI_URL);
    return false;
  }
}

// Create About page
async function createAbout() {
  console.log('Creating About page...');

  const result = await apiRequest('PUT', '/about', { data: aboutData });

  if (result) {
    console.log('✓ Created About page');
  }

  return result?.data;
}

// Create Privacy Policy page
async function createPrivacyPolicy() {
  console.log('Creating Privacy Policy page...');

  const result = await apiRequest('PUT', '/privacy-policy', {
    data: privacyPolicyData,
  });

  if (result) {
    console.log('✓ Created Privacy Policy page');
  }

  return result?.data;
}

// Main seeding function
async function seedSingleTypes() {
  console.log('🌱 Starting single type content seeding...');
  console.log('📡 Strapi URL:', STRAPI_URL);

  const isRunning = await checkStrapi();
  if (!isRunning) {
    process.exit(1);
  }

  try {
    await createAbout();
    await createPrivacyPolicy();

    console.log('🎉 Single type content seeding completed successfully!');
    console.log('📖 Visit http://localhost:1337/admin to manage your content');
    console.log('🌐 API available at http://localhost:1337/api');
  } catch (error) {
    console.error('❌ Seeding failed:', error.message);
    process.exit(1);
  }
}

// Run the seeder
if (require.main === module) {
  seedSingleTypes();
}

module.exports = { seedSingleTypes };
