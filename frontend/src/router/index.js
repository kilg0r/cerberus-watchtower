import { createRouter, createWebHistory } from 'vue-router'
import ReviewQueueView from '../views/ReviewQueueView.vue'
import ActivityView from '../views/ActivityView.vue'

const routes = [
  { path: '/', redirect: '/review' },
  { path: '/review', name: 'review', component: ReviewQueueView },
  { path: '/activity', name: 'activity', component: ActivityView },
  {
    path: '/artifacts',
    name: 'artifacts',
    // lazy: keeps the markdown renderer out of the main bundle
    component: () => import('../views/ArtifactsView.vue'),
  },
  {
    path: '/architecture',
    name: 'architecture',
    // lazy: keeps cytoscape out of the main bundle
    component: () => import('../views/ArchitectureView.vue'),
  },
  {
    path: '/history',
    name: 'history',
    // lazy: the bundle review pulls in cytoscape for the arch-delta drill-through
    component: () => import('../views/GitHistoryView.vue'),
  },
  {
    path: '/ports',
    name: 'ports',
    component: () => import('../views/PortsView.vue'),
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
