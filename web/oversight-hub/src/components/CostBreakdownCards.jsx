/**
 * CostBreakdownCards.jsx
 *
 * Cost Breakdown Visualization Component
 * Displays cost distribution by pipeline phase and AI model
 *
 * Integrates with Phase 1 cost calculator data:
 * - cost_by_phase: research, draft, assess, refine, finalize
 * - cost_by_model: ollama, gpt-3.5, gpt-4, claude
 *
 * Used in: ExecutiveDashboard, CostMetricsDashboard
 * Data source: analytics/kpis endpoint or dedicated cost endpoints
 *
 * Props:
 *  - costByPhase (object): { phase: cost }
 *  - costByModel (object): { model: cost }
 */

import React from 'react';
import PropTypes from 'prop-types';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Grid,
  LinearProgress,
  useTheme,
} from '@mui/material';
import { getPhaseColor, getModelColor } from '../lib/muiStyles';

const CostBreakdownCards = ({ costByPhase = {}, costByModel = {} }) => {
  const theme = useTheme();

  // Calculate totals and percentages for phases
  const totalPhase = Object.values(costByPhase).reduce(
    (sum, val) => sum + (val || 0),
    0
  );
  const phaseItems = Object.entries(costByPhase)
    .map(([phase, cost]) => ({
      phase: phase.charAt(0).toUpperCase() + phase.slice(1),
      lowerPhase: phase.toLowerCase(),
      cost,
      percentage: totalPhase > 0 ? ((cost / totalPhase) * 100).toFixed(1) : 0,
    }))
    .filter((item) => item.cost > 0)
    .sort((a, b) => b.cost - a.cost);

  // Calculate totals and percentages for models
  const totalModel = Object.values(costByModel).reduce(
    (sum, val) => sum + (val || 0),
    0
  );
  const modelItems = Object.entries(costByModel)
    .map(([model, cost]) => ({
      model: model.charAt(0).toUpperCase() + model.slice(1),
      lowerModel: model.toLowerCase(),
      cost,
      percentage: totalModel > 0 ? ((cost / totalModel) * 100).toFixed(1) : 0,
    }))
    .filter((item) => item.cost > 0)
    .sort((a, b) => b.cost - a.cost);

  if (
    Object.keys(costByPhase).length === 0 &&
    Object.keys(costByModel).length === 0
  ) {
    return (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          py: 5,
          px: 2,
          color: theme.palette.text.secondary,
        }}
      >
        <Typography variant="body1">No cost data available</Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ width: '100%' }}>
      <Typography
        variant="h5"
        sx={{
          mb: 3,
          fontWeight: 'bold',
          color: theme.palette.text.primary,
        }}
      >
        💰 Cost Breakdown Analysis
      </Typography>

      <Grid
        container
        spacing={3}
        sx={{
          mb: 3,
        }}
      >
        {/* Cost by Phase */}
        {phaseItems.length > 0 && (
          <Grid size={{ xs: 12, sm: 6 }}>
            <Card
              sx={{
                backgroundColor: theme.palette.background.paper,
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              <CardContent sx={{ flexGrow: 1 }}>
                <Box
                  sx={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    mb: 2,
                  }}
                >
                  <Typography variant="h6" sx={{ fontWeight: 'bold' }}>
                    By Pipeline Phase
                  </Typography>
                  <Typography
                    variant="h6"
                    sx={{
                      color: getPhaseColor('research'),
                      fontWeight: 'bold',
                    }}
                  >
                    ${totalPhase.toFixed(6)}
                  </Typography>
                </Box>

                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  {phaseItems.map((item) => (
                    <Box key={item.lowerPhase}>
                      <Box
                        sx={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          mb: 0.5,
                        }}
                      >
                        <Box
                          sx={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 1,
                          }}
                        >
                          <Box
                            sx={{
                              width: 8,
                              height: 8,
                              borderRadius: '50%',
                              backgroundColor: getPhaseColor(item.lowerPhase),
                            }}
                          />
                          <Typography variant="body2">{item.phase}</Typography>
                        </Box>
                        <Box
                          sx={{
                            display: 'flex',
                            gap: 1,
                            alignItems: 'center',
                          }}
                        >
                          <Typography
                            variant="body2"
                            sx={{
                              color: theme.palette.text.secondary,
                              minWidth: '40px',
                              textAlign: 'right',
                            }}
                          >
                            {item.percentage}%
                          </Typography>
                          <Typography
                            variant="body2"
                            sx={{
                              fontWeight: 'bold',
                              minWidth: '80px',
                              textAlign: 'right',
                            }}
                          >
                            ${item.cost.toFixed(6)}
                          </Typography>
                        </Box>
                      </Box>
                      <LinearProgress
                        variant="determinate"
                        value={Number(item.percentage)}
                        sx={{
                          backgroundColor: 'rgba(255, 255, 255, 0.1)',
                          '& .MuiLinearProgress-bar': {
                            backgroundColor: getPhaseColor(item.lowerPhase),
                          },
                        }}
                      />
                    </Box>
                  ))}
                </Box>

                <Typography
                  variant="caption"
                  sx={{
                    display: 'block',
                    mt: 2,
                    color: theme.palette.text.secondary,
                  }}
                >
                  Cost calculation based on phase token estimates and model
                  pricing
                </Typography>
              </CardContent>
            </Card>
          </Grid>
        )}

        {/* Cost by Model */}
        {modelItems.length > 0 && (
          <Grid size={{ xs: 12, sm: 6 }}>
            <Card
              sx={{
                backgroundColor: theme.palette.background.paper,
                height: '100%',
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              <CardContent sx={{ flexGrow: 1 }}>
                <Box
                  sx={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    mb: 2,
                  }}
                >
                  <Typography variant="h6" sx={{ fontWeight: 'bold' }}>
                    By AI Model
                  </Typography>
                  <Typography
                    variant="h6"
                    sx={{
                      color: getModelColor('ollama'),
                      fontWeight: 'bold',
                    }}
                  >
                    ${totalModel.toFixed(6)}
                  </Typography>
                </Box>

                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  {modelItems.map((item) => (
                    <Box key={item.lowerModel}>
                      <Box
                        sx={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          mb: 0.5,
                        }}
                      >
                        <Box
                          sx={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 1,
                          }}
                        >
                          <Box
                            sx={{
                              width: 8,
                              height: 8,
                              borderRadius: '50%',
                              backgroundColor: getModelColor(item.lowerModel),
                            }}
                          />
                          <Typography variant="body2">{item.model}</Typography>
                        </Box>
                        <Box
                          sx={{
                            display: 'flex',
                            gap: 1,
                            alignItems: 'center',
                          }}
                        >
                          <Typography
                            variant="body2"
                            sx={{
                              color: theme.palette.text.secondary,
                              minWidth: '40px',
                              textAlign: 'right',
                            }}
                          >
                            {item.percentage}%
                          </Typography>
                          <Typography
                            variant="body2"
                            sx={{
                              fontWeight: 'bold',
                              minWidth: '80px',
                              textAlign: 'right',
                            }}
                          >
                            ${item.cost.toFixed(6)}
                          </Typography>
                        </Box>
                      </Box>
                      <LinearProgress
                        variant="determinate"
                        value={Number(item.percentage)}
                        sx={{
                          backgroundColor: 'rgba(255, 255, 255, 0.1)',
                          '& .MuiLinearProgress-bar': {
                            backgroundColor: getModelColor(item.lowerModel),
                          },
                        }}
                      />
                    </Box>
                  ))}
                </Box>

                <Typography
                  variant="caption"
                  sx={{
                    display: 'block',
                    mt: 2,
                    color: theme.palette.text.secondary,
                  }}
                >
                  Pricing: Ollama free | GPT-3.5 $0.00175/1K | GPT-4 $0.045/1K |
                  Claude $0.015-$0.045/1K
                </Typography>
              </CardContent>
            </Card>
          </Grid>
        )}
      </Grid>

      {/* Cost Summary Stats */}
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, sm: 4 }}>
          <Card sx={{ backgroundColor: theme.palette.background.paper }}>
            <CardContent>
              <Typography
                variant="body2"
                sx={{ color: theme.palette.text.secondary }}
              >
                Total Phase Cost
              </Typography>
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 'bold',
                  color: getPhaseColor('research'),
                  mt: 1,
                }}
              >
                ${totalPhase.toFixed(6)}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 12, sm: 4 }}>
          <Card sx={{ backgroundColor: theme.palette.background.paper }}>
            <CardContent>
              <Typography
                variant="body2"
                sx={{ color: theme.palette.text.secondary }}
              >
                Total Model Cost
              </Typography>
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 'bold',
                  color: getModelColor('ollama'),
                  mt: 1,
                }}
              >
                ${totalModel.toFixed(6)}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 12, sm: 4 }}>
          <Card sx={{ backgroundColor: theme.palette.background.paper }}>
            <CardContent>
              <Typography
                variant="body2"
                sx={{ color: theme.palette.text.secondary }}
              >
                Combined Cost
              </Typography>
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 'bold',
                  color: theme.palette.primary.main,
                  mt: 1,
                }}
              >
                ${(totalPhase + totalModel).toFixed(6)}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
};

CostBreakdownCards.propTypes = {
  costByPhase: PropTypes.objectOf(PropTypes.number),
  costByModel: PropTypes.objectOf(PropTypes.number),
};

export default CostBreakdownCards;
