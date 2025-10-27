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
  title: 'About Glad Labs',
  subtitle: 'Building the AI Co-Founder of Tomorrow',
  content: `<h2>Who We Are</h2>
<p>Glad Labs is revolutionizing how businesses operate by creating the world's first truly autonomous AI Co-Founder system. We combine cutting-edge artificial intelligence with practical business automation to give every entrepreneur access to intelligent strategic guidance and operational excellence.</p>

<h2>Our Vision</h2>
<p>We envision a future where sophisticated AI partners work alongside human entrepreneurs, handling complex business operations, content creation, market analysis, and financial management—allowing founders to focus on vision and growth.</p>

<h2>What We Build</h2>
<ul>
<li><strong>Intelligent Business Agents:</strong> Specialized AI systems for content, finance, compliance, and market insights</li>
<li><strong>Autonomous Content Engine:</strong> Generate on-brand, SEO-optimized content across all platforms</li>
<li><strong>Business Intelligence Dashboard:</strong> Real-time insights into business metrics and performance</li>
<li><strong>Multi-Agent Orchestration:</strong> Coordinate complex workflows with precision and intelligence</li>
<li><strong>Voice-Enabled Interface:</strong> Control your AI co-founder naturally through conversation</li>
</ul>`,
  mission: `<h3>Our Mission</h3>
<p>To democratize access to enterprise-grade AI automation and intelligent business operations, enabling entrepreneurs and small businesses to compete at scale.</p>
<p>We believe that every business—regardless of size or resources—deserves access to the same sophisticated tools and insights that power Fortune 500 companies.</p>`,
  vision: `<h3>Our Vision</h3>
<p>A world where artificial intelligence empowers human creativity and strategic thinking rather than replacing it. Where business owners spend their time on vision and innovation, and their AI co-founder handles the operational complexity.</p>`,
  values: `<h3>Our Core Values</h3>
<ul>
<li><strong>🚀 Innovation:</strong> We push the boundaries of what's possible with AI, constantly exploring new frontiers in autonomous systems</li>
<li><strong>🤝 Collaboration:</strong> The best AI works alongside humans, not against them. We build tools that enhance human capability</li>
<li><strong>⚖️ Responsibility:</strong> We develop AI ethically, transparently, and with strong safeguards against misuse</li>
<li><strong>🎯 Excellence:</strong> We obsess over quality, reliability, and the user experience in everything we build</li>
<li><strong>💡 Accessibility:</strong> Powerful AI shouldn't be behind paywalls or exclusive to tech giants—it should be available to all</li>
</ul>`,
  seo: {
    metaTitle: 'About Glad Labs - AI Co-Founder System',
    metaDescription:
      'Glad Labs is building the AI Co-Founder of tomorrow—autonomous business agents that handle content, finance, compliance, and operations so entrepreneurs can focus on growth.',
    keywords:
      'AI co-founder, business automation, AI agents, entrepreneurship, content automation, business intelligence',
  },
};

// Sample Privacy Policy data
const privacyPolicyData = {
  title: 'Privacy Policy',
  effectiveDate: '2024-10-01',
  lastUpdated: new Date().toISOString(),
  contactEmail: 'privacy@gladlabs.com',
  content: `<h2>Privacy Policy for Glad Labs</h2>

<p><strong>Last Updated:</strong> October 23, 2025</p>
<p><strong>Effective Date:</strong> October 1, 2024</p>

<h3>1. Introduction</h3>
<p>Glad Labs, LLC ("we," "us," "our," or "Company") is committed to protecting your privacy. This Privacy Policy explains how we collect, use, disclose, and safeguard your information when you visit our website, use our services, and interact with our AI Co-Founder system.</p>

<p>Please read this Privacy Policy carefully. If you do not agree with our policies and practices, please do not use our services.</p>

<h3>2. Information We Collect</h3>

<h4>2.1 Information You Provide</h4>
<ul>
<li><strong>Account Information:</strong> Name, email address, company name, job title, and phone number</li>
<li><strong>Profile Information:</strong> Professional background, preferences, and usage preferences</li>
<li><strong>Payment Information:</strong> Billing address, payment method details (processed securely through third-party processors)</li>
<li><strong>Communications:</strong> Messages, feedback, support requests, and correspondence with our team</li>
<li><strong>Content:</strong> Documents, files, and content you upload or create using our services</li>
</ul>

<h4>2.2 Automatically Collected Information</h4>
<ul>
<li><strong>Device Information:</strong> Device type, operating system, browser type and version, IP address</li>
<li><strong>Usage Data:</strong> Pages visited, features used, time spent, click patterns, and interaction sequences</li>
<li><strong>Location Data:</strong> General location based on IP address (city, region, country level)</li>
<li><strong>Cookies & Tracking:</strong> Session identifiers, preferences, and analytics data</li>
<li><strong>API Interactions:</strong> Requests made to our AI agents, prompts submitted, and system responses</li>
</ul>

<h4>2.3 Third-Party Information</h4>
<p>We may receive information about you from third parties including analytics providers, business partners, and public sources to enhance and verify the accuracy of our records.</p>

<h3>3. How We Use Your Information</h3>
<p>We use collected information for the following purposes:</p>
<ul>
<li><strong>Service Delivery:</strong> Providing, maintaining, and improving our AI Co-Founder system and related services</li>
<li><strong>Personalization:</strong> Tailoring AI responses, recommendations, and features to your preferences</li>
<li><strong>Communication:</strong> Sending service updates, support messages, promotional content, and security alerts</li>
<li><strong>Analytics:</strong> Understanding usage patterns, optimizing user experience, and improving our platform</li>
<li><strong>Compliance:</strong> Meeting legal obligations, enforcing agreements, and protecting against fraud</li>
<li><strong>Security:</strong> Detecting, preventing, and addressing technical and security issues</li>
<li><strong>Business Operations:</strong> Administrative tasks, billing, and business development</li>
</ul>

<h3>4. Information Sharing & Disclosure</h3>

<h4>4.1 Service Providers</h4>
<p>We share information with third-party service providers who perform services on our behalf, including:</p>
<ul>
<li>Cloud infrastructure providers (for hosting and data storage)</li>
<li>Payment processors (for billing and financial transactions)</li>
<li>Analytics and monitoring services</li>
<li>AI/ML model providers (for enhanced intelligence capabilities)</li>
<li>Communication platforms</li>
</ul>

<h4>4.2 Business Partners</h4>
<p>We may share aggregated, anonymized data with business partners for research, marketing, and service improvements.</p>

<h4>4.3 Legal Requirements</h4>
<p>We may disclose your information when required by law, court order, government request, or to protect our legal rights, safety, and security.</p>

<h4>4.4 Business Transfers</h4>
<p>In the event of merger, acquisition, bankruptcy, or sale of assets, your information may be transferred as part of that transaction.</p>

<h4>4.5 With Your Consent</h4>
<p>We may share information with third parties when you explicitly consent to such sharing.</p>

<h3>5. Data Security</h3>
<p>We implement comprehensive security measures to protect your information:</p>
<ul>
<li><strong>Encryption:</strong> SSL/TLS encryption for data in transit, AES-256 for data at rest</li>
<li><strong>Access Controls:</strong> Role-based access, multi-factor authentication, and principle of least privilege</li>
<li><strong>Regular Audits:</strong> Periodic security assessments, vulnerability scanning, and penetration testing</li>
<li><strong>Employee Training:</strong> All staff undergo data protection and security awareness training</li>
<li><strong>Incident Response:</strong> Established procedures for detecting, responding to, and reporting security incidents</li>
</ul>

<p><strong>Important:</strong> No security system is completely impenetrable. While we strive to protect your information, we cannot guarantee absolute security.</p>

<h3>6. Your Rights & Choices</h3>

<h4>6.1 Access & Portability</h4>
<p>You have the right to request a copy of your personal information in a portable format.</p>

<h4>6.2 Correction & Deletion</h4>
<p>You may request correction of inaccurate information or deletion of your data (subject to legal retention requirements).</p>

<h4>6.3 Opt-Out Options</h4>
<p>You can opt out of promotional emails by clicking "unsubscribe" in our communications or updating your notification preferences.</p>

<h4>6.4 Cookie Preferences</h4>
<p>Most browsers allow you to control cookies. You can delete cookies and disable future cookie storage through your browser settings.</p>

<h4>6.5 Do Not Track</h4>
<p>Some browsers include a "Do Not Track" feature. Our services do not currently respond to DNT signals, but you can control data collection through browser settings.</p>

<h3>7. Regional Privacy Rights</h3>

<h4>7.1 California Residents (CCPA/CPRA)</h4>
<p>You have the right to:</p>
<ul>
<li>Know what personal information is collected and how it's used</li>
<li>Delete personal information (with exceptions)</li>
<li>Opt-out of the sale or sharing of personal information</li>
<li>Non-discrimination for exercising CCPA rights</li>
</ul>

<h4>7.2 European Residents (GDPR)</h4>
<p>If you are in the EU, you have rights including:</p>
<ul>
<li>Right of access to your personal data</li>
<li>Right to rectification of inaccurate data</li>
<li>Right to erasure ("right to be forgotten")</li>
<li>Right to restrict processing</li>
<li>Right to data portability</li>
<li>Right to object to processing</li>
<li>Right to lodge complaints with supervisory authorities</li>
</ul>

<h3>8. Data Retention</h3>
<p>We retain personal information only as long as necessary for:</p>
<ul>
<li>Providing our services</li>
<li>Meeting legal and regulatory requirements</li>
<li>Resolving disputes</li>
<li>Enforcing our agreements</li>
<li>Business operations and record-keeping</li>
</ul>

<p>Retention periods vary depending on the type of information and its purpose. You may request deletion of your data at any time (subject to legal obligations).</p>

<h3>9. International Data Transfers</h3>
<p>Your information may be transferred to, stored in, and processed in countries other than your country of residence, including the United States. These countries may have different data protection laws than your home country.</p>

<p>By using our services, you consent to the transfer of your information to countries outside your country of residence, which may have different data protection rules.</p>

<h3>10. Children's Privacy</h3>
<p>Our services are not intended for children under 13 years of age. We do not knowingly collect personal information from children under 13. If we become aware that we have collected information from a child under 13, we will take steps to delete such information promptly.</p>

<p>Parents or guardians who believe their child's information has been collected should contact us immediately at privacy@gladlabs.com.</p>

<h3>11. Third-Party Links & Services</h3>
<p>Our website and services may contain links to third-party websites and services. This Privacy Policy does not apply to third-party services, and we are not responsible for their privacy practices. We encourage you to review the privacy policies of any third-party services before providing personal information.</p>

<h3>12. Automated Decision-Making & AI Processing</h3>
<p>Our services use AI algorithms for:</p>
<ul>
<li>Content generation and optimization</li>
<li>Personalization and recommendations</li>
<li>Fraud detection and security analysis</li>
<li>Usage analytics and insights</li>
</ul>

<p>You have the right to request human review of any automated decisions that significantly affect you, and to understand how our AI systems use your information.</p>

<h3>13. Updates to This Privacy Policy</h3>
<p>We may update this Privacy Policy periodically to reflect changes in our practices, technology, legal requirements, or other factors. We will notify you of material changes by updating the "Last Updated" date and posting the revised policy on our website.</p>

<p>Your continued use of our services following the posting of a revised Privacy Policy constitutes your acceptance of the changes.</p>

<h3>14. Contact Information</h3>
<p>If you have questions about this Privacy Policy or our privacy practices, please contact us at:</p>

<p><strong>Glad Labs Privacy Officer</strong><br />
Email: privacy@gladlabs.com<br />
Address: Glad Labs, LLC, San Francisco, CA<br />
Phone: Available upon request</p>

<p>We will respond to privacy inquiries within 30 days.</p>

<h3>15. Regulatory Compliance</h3>
<p>This Privacy Policy is designed to comply with:</p>
<ul>
<li>California Consumer Privacy Act (CCPA)</li>
<li>California Privacy Rights Act (CPRA)</li>
<li>General Data Protection Regulation (GDPR)</li>
<li>Children's Online Privacy Protection Act (COPPA)</li>
<li>CAN-SPAM Act</li>
<li>Other applicable privacy laws</li>
</ul>`,
  seo: {
    metaTitle: 'Privacy Policy - Glad Labs',
    metaDescription:
      'Glad Labs Privacy Policy: Learn how we collect, use, and protect your personal information. CCPA and GDPR compliant.',
    keywords:
      'privacy policy, data protection, CCPA, GDPR, personal information, AI privacy, data security',
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
