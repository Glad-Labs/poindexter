import React, { useState } from 'react';
import { Container, Box, Tabs, Tab, Paper } from '@mui/material';
import CapabilitiesBrowser from '../components/marketplace/CapabilitiesBrowser';
import ServiceExplorer from '../components/marketplace/ServiceExplorer';
import WorkflowBuilder from '../components/marketplace/WorkflowBuilder';

function TabPanel(props) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`marketplace-tabpanel-${index}`}
      aria-labelledby={`marketplace-tab-${index}`}
      {...other}
    >
      {value === index && <Box sx={{ p: 3 }}>{children}</Box>}
    </div>
  );
}

function Marketplace() {
  const [activeTab, setActiveTab] = useState(0);

  const handleTabChange = (event, newValue) => {
    setActiveTab(newValue);
  };

  return (
    <Container maxWidth="xl" sx={{ py: 4 }}>
      <Paper sx={{ width: '100%' }}>
        <Tabs
          value={activeTab}
          onChange={handleTabChange}
          aria-label="marketplace navigation"
          sx={{ borderBottom: 1, borderColor: 'divider' }}
        >
          <Tab
            label="Capabilities"
            id="marketplace-tab-0"
            aria-controls="marketplace-tabpanel-0"
          />
          <Tab
            label="Services"
            id="marketplace-tab-1"
            aria-controls="marketplace-tabpanel-1"
          />
          <Tab
            label="Workflows"
            id="marketplace-tab-2"
            aria-controls="marketplace-tabpanel-2"
          />
        </Tabs>

        <TabPanel value={activeTab} index={0}>
          <CapabilitiesBrowser />
        </TabPanel>

        <TabPanel value={activeTab} index={1}>
          <ServiceExplorer />
        </TabPanel>

        <TabPanel value={activeTab} index={2}>
          <WorkflowBuilder />
        </TabPanel>
      </Paper>
    </Container>
  );
}

export default Marketplace;
