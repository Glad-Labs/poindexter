/**
 * Create missing content types via Strapi Content-Type Builder API
 * This will register the content types properly in Strapi using admin API
 */

const fetch = require('node-fetch');

const baseURL = 'http://localhost:1337';
const adminURL = `${baseURL}/admin`;

async function getAdminToken() {
  // For this demo, we'll use a simple approach
  // In production, you'd use proper authentication
  console.log('🔑 Using admin API endpoint for content type creation...');
  return null; // We'll use a different approach
}

async function createContentTypeViaAPI(contentTypeData) {
  try {
    const response = await fetch(
      `${adminURL}/content-type-builder/content-types`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(contentTypeData),
      }
    );

    if (response.ok) {
      const result = await response.json();
      console.log(
        `✅ Created content type: ${contentTypeData.contentType.displayName}`
      );
      return result;
    } else {
      const error = await response.text();
      console.log(
        `❌ Failed to create ${contentTypeData.contentType.displayName}: ${response.status} ${error}`
      );
      return null;
    }
  } catch (error) {
    console.log(
      `❌ Error creating ${contentTypeData.contentType.displayName}: ${error.message}`
    );
    return null;
  }
}

async function createContentTypes() {
  try {
    console.log('🏗️ Creating missing content types via API...\n');

    // Author content type
    const authorSchema = {
      contentType: {
        displayName: 'Author',
        singularName: 'author',
        pluralName: 'authors',
        description:
          'Represents content authors, including AI agents and human writers for the GLAD Labs content engine.',
        kind: 'collectionType',
        attributes: {
          Name: {
            type: 'string',
            required: true,
            description: 'Full name of the author',
          },
          Bio: {
            type: 'text',
            description: 'Brief biography or description of the author',
          },
          Avatar: {
            type: 'media',
            multiple: false,
            allowedTypes: ['images'],
            description: 'Profile picture or avatar',
          },
        },
        options: {
          draftAndPublish: false,
        },
      },
    };

    // Tag content type
    const tagSchema = {
      contentType: {
        displayName: 'Tag',
        singularName: 'tag',
        pluralName: 'tags',
        description: 'Content tags for categorization and SEO purposes.',
        kind: 'collectionType',
        attributes: {
          Name: {
            type: 'string',
            required: true,
            description: 'Tag name',
          },
          Slug: {
            type: 'uid',
            targetField: 'Name',
            description: 'URL-friendly version of the tag name',
          },
          Description: {
            type: 'text',
            description: 'Optional description of what this tag represents',
          },
        },
        options: {
          draftAndPublish: false,
        },
      },
    };

    // Content Metric content type
    const metricSchema = {
      contentType: {
        displayName: 'Content Metric',
        singularName: 'content-metric',
        pluralName: 'content-metrics',
        description:
          'Performance tracking for published content, including views, engagement, and AI generation metrics.',
        kind: 'collectionType',
        attributes: {
          Views: {
            type: 'integer',
            default: 0,
            description: 'Total number of views for this content',
          },
          Likes: {
            type: 'integer',
            default: 0,
            description: 'Number of likes or upvotes',
          },
          Shares: {
            type: 'integer',
            default: 0,
            description: 'Number of shares across social platforms',
          },
          Comments: {
            type: 'integer',
            default: 0,
            description: 'Number of comments on the content',
          },
        },
        options: {
          draftAndPublish: false,
        },
      },
    };

    console.log('� Creating Author content type...');
    await createContentTypeViaAPI(authorSchema);

    console.log('📋 Creating Tag content type...');
    await createContentTypeViaAPI(tagSchema);

    console.log('📋 Creating Content Metric content type...');
    await createContentTypeViaAPI(metricSchema);

    console.log('\n🎯 Content type creation complete!');
    console.log(
      '🔄 Strapi will restart automatically to register the new content types.'
    );
    console.log(
      '📝 After restart, check the permissions page to see all content types.'
    );
  } catch (error) {
    console.error('❌ Error:', error.message);
  }
}

if (require.main === module) {
  createContentTypes();
}

module.exports = { createContentTypes };
