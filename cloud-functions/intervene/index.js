const { PubSub } = require('@google-cloud/pubsub');

exports.intervene = async (req, res) => {
  // Set CORS headers for preflight requests
  res.set('Access-Control-Allow-Origin', '*');
  res.set('Access-Control-Allow-Methods', 'POST');
  res.set('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    // Send response to preflight OPTIONS requests
    res.status(204).send('');
    return;
  }

  const pubsub = new PubSub();
  const topicName = 'agent-commands';

  // The function will now forward the JSON body from the request
  const messagePayload = req.body;
  if (!messagePayload || !messagePayload.command) {
    return res.status(400).send('Bad Request: Missing command in request body.');
  }

  const dataBuffer = Buffer.from(JSON.stringify(messagePayload));

  try {
    const messageId = await pubsub.topic(topicName).publishMessage({ data: dataBuffer });
    console.log(`Message ${messageId} published with payload:`, messagePayload);
    res.status(200).send(`Command sent: ${messagePayload.command}`);
  } catch (error) {
    console.error(`Received error while publishing: ${error.message}`);
    res.status(500).send(`Error publishing message: ${error.message}`);
  }
};
